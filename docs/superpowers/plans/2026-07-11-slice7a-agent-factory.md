# Slice 7A — Agent Factory + Asymmetric-Pairing Test + Continuous-Training Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully automated local pipeline that produces, evaluates, versions, and submits agents to the Kaggle ladder (candidate queue -> evaluation runner -> gate/submitter -> harvester -> training daemon), seeded by the asymmetric-pairing discriminating test, with every step auto-logging Strategy-writeup material.

**Architecture:** A new importable package `src/ptcg/factory/` (candidates ledger, evaluation runner, bundle builder, Kaggle client/harvester, gate/submitter, cycle, training daemon) with thin CLI wrappers in `scripts/`; it reuses the existing arena (`ptcg.arena.runner`), packaging (`scripts/package_submission.py`), and training (`scripts/generate_training_data.py` / `scripts/train_value_net.py`) machinery rather than duplicating it. The asymmetric test (spec section 7) runs FIRST and its recorded decision seeds factory priorities.

**Tech Stack:** Python 3.11+ / uv, stdlib dataclasses + json (repo style — no pydantic), pytest (flat `tests/` dir), torch dev-only for training, Kaggle CLI via `uvx kaggle`, Windows Task Scheduler (PowerShell) for the nightly cycle.

**Spec:** `docs/superpowers/specs/2026-07-11-slice7a-agent-factory-design.md`

## Global Constraints

- `encoding="utf-8"` on EVERY text write (guard-test enforced — `tests/test_experiments_append.py` is extended in Task 3 to scan every factory module and factory script; Windows cp1252 truncate-then-crash hazard).
- No torch/numpy imports anywhere under `src/ptcg/` that ships in bundles (serve-time is pure stdlib). Torch stays in `scripts/` + `src/ptcg/train/` (dev-only). Task 6 adds an executable no-torch/numpy-in-bundle scan that runs on every built bundle.
- Kaggle hard cap 5 submissions/day enforced by a persistent counter that survives restarts (`experiments/factory/submission_counter.json`, reconciled against harvested ladder rows every cycle).
- Quote all paths — the repo path contains a space (`"C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy"`).
- Orchestrator owns long-running background processes — any task whose job includes waiting on a multi-minute run (T2, T12 acceptance, T13) must scope the implementer to setup + harvest only (`.claude/rules/background-arena-execution.md`). Dry-run every long arena command with `--games 1` before committing to the full run.
- All plan-authored test vectors must be hand-verified executably before transcription (`.claude/rules/plan-test-arithmetic-sanity.md`). Every numeric vector in this plan was verified by running the arithmetic in Python on 2026-07-11 (differential CI, pooled WRs, gate margins, tournament priorities); implementers re-verify before transcribing.
- Any metric/matrix code gets a complexity glance at plan scale, and the first production-scale run counts as a test. Complexity audit of this plan: every factory routine is O(n) in candidates/ledger-rows/EXPERIMENTS-lines (n < 10^4); no pairwise/matrix shapes anywhere. The first real cycle (T13) and first real training cycle (T12) are explicitly treated as production-scale tests.
- Repo conventions: tests live in the flat `tests/` directory named `test_<module>.py` (NOT next to code — repo convention wins over the global vertical-slice default); ruff line length 100; type hints on signatures; stdlib dataclasses.
- The CURRENT ladder identity stays untouched until the factory itself submits something better: `src/ptcg/submission_main.py` and `src/ptcg/agents/current.py` (the repo files) are never modified by this slice. The bundle builder rewrites only the STAGED copy of `ptcg/agents/current.py` inside a candidate bundle.
- Arena series that feed decisions go through `scripts/run_arena.py` (auto-appends `experiments/EXPERIMENTS.md`); the factory evaluation runner appends its own rows via the same `SeriesStats.markdown_row` convention with notes prefix `slice7a-factory-eval`.
- Stochastic results near a decision bar follow `.claude/rules/stochastic-gate-replication.md` (replicate before recording; report all runs).
- Per the spec's retired-rule decision: factory submissions are AUTO-APPROVED (no per-submission human sign-off); the versioned-identity description standard + digest + pause file replace it. T13's real upload proceeds without an AskUserQuestion.

## Spec coverage map

| Spec section | Task(s) |
|---|---|
| S1 system overview | T3–T12 (stages), T9 (wiring) |
| S2 candidate model | T3 (model), T4 (seed pool) |
| S3 local evaluation | T5 |
| S4 submission policy | T8 |
| S5 continuous harvest + episode-data investigation | T7 |
| S6 writeup capture (LADDER.md / EXPERIMENTS.md / writeup-notes.md) | T7, T5, T9, T14 |
| S7 asymmetric-pairing test (runs FIRST) | T1 (pre-registration), T2 (execution + decision) |
| S8 continuous-training daemon | T11 (core), T12 (PerDeckNetTrainer) |
| S9 error handling and testing | T3 (guard), T5 (crash isolation), T6 (verify/no-torch), T8 (counter/retry), T9 (dry-run e2e) |
| S10 out of scope | honored: no bandit math (T7 hook is a fixed +/-0.05 rule), no auto-evolved decks, no 7B trainer internals |
| Success criterion 1 | T2 |
| Success criterion 2 | T9 (dry-run green) + T13 (real cycle) |
| Success criterion 3 | T12 |
| Success criterion 4 | T7/T9/T13 (auto-populated ledgers) |
| Success criterion 5 | T14 |

## Task phases

```
Phase A (test first, spec S7):  T1 pre-registration + differential module
                                T2 [ORCH] execute 12 cells + record decision
Phase B (factory core):         T3 candidate model/ledger   T4 seed pool
                                T5 evaluation runner         T6 bundle builder
                                T7 kaggle client + harvester T8 gate + submitter
                                T9 factory cycle (dry-run e2e green)
Phase C (ops + training):       T10 scheduling + ops docs
                                T11 daemon core              T12 PerDeckNetTrainer + [ORCH] real training cycle
Phase D (proof + close):        T13 [ORCH] first real automated cycle (real upload)
                                T14 finalization
```

Combined-review eligible (objective carve-out: <50 LOC of judgment-free/mechanical content, no new module boundary): **T4** (seed script — mechanical, priorities computed from an existing ledger), **T10** (PowerShell registration + docs), **T14** (docs + coherence test). T2 and T13 are orchestrator-execution tasks whose review is verification of the recorded analysis against raw artifacts. All other tasks get two-stage review.

Key existing-interface landmarks (implementers: grep these before writing code; report `plan-drift` if any mismatch):

- `scripts/run_arena.py` — `AGENTS` dict has kinds `random|heuristic|search|search-net`; `--net-weights` renames agent to `search-net-<stem>`; appends `experiments/EXPERIMENTS.md` via `stats.markdown_row(date, agent_a, agent_b, deck_a, deck_b, notes)`.
- `src/ptcg/arena/stats.py` — `SeriesStats(wins_a, wins_b, draws, game_seconds, max_move_seconds)`, properties `n`, `win_rate_a`, `ci_a` (`wilson_ci`), method `markdown_row` producing a 13-column row.
- `scripts/package_submission.py` — `build_bundle(deck_path: Path, out_dir: Path) -> Path` (stages `out_dir/submission/`, tars to `out_dir/submission.tar.gz`), `verify_bundle(tar_path) -> list[str]`, `smoke_bundle(staging_dir) -> None` (full-battle subprocess smoke, 120 s timeout). `scripts/` is an importable package (`scripts/__init__.py` exists).
- `scripts/generate_training_data.py` — `deck_pairings(deck_dir)` currently iterates ALL 9 candidate decks (NOT deck-parametrizable today; T12 adds `--decks`). `run(games_per_pairing, out, seed, agent_kind, budget_ms, gate)`.
- `scripts/train_value_net.py` — `--data <jsonl...> --out <weights.json> --epochs --hidden --batch --lr --patience --seed`; exports the stdlib-JSON serving format `ValueNetEvaluator.load()` reads.
- `src/ptcg/agents/current.py` — `make_current_agent(deck) -> Agent`, `CURRENT_AGENT_NAME = "heuristic-v0"`; `HeuristicAgent.name == "heuristic-v0"`.
- `src/ptcg/search/searcher.py` — `SearchConfig` fields include `rollout_depth`, `deviate_min_visits`, `deviate_value_edge`, `final_move_rule`, `use_root_prior`, `prior_tau`, `c_puct`.
- `src/ptcg/agents/search_agent.py` — `SearchAgent(deck, config=, time_manager=, backend=, evaluator=, collect_stats=)`.
- Slice-3 tournament ledger: `experiments/tournament/results.json` (deck-hash map + per-pairing win counts).

---

### Task 1: Asymmetric-test pre-registration + differential module + command matrix

Pre-register the decision criteria and land the analysis code BEFORE any runs (the analysis is part of the pre-registration — it cannot be tuned after seeing data).

**Pairings (pre-registered here, derived executably from `experiments/tournament/results.json` on 2026-07-11):**

Pooled Slice-3 WRs: mega-lucario-fighting 411/550 = 0.747 (strong), mega-lucario-v4 338/500 = 0.676, mega-starmie-water 439/650 = 0.675, mega-lucario-v3 450/850 = 0.529 (mid), mega-mawile-metal 168/400 = 0.420 (weak).

- **P1 strong-vs-weak:** mega-lucario-fighting vs mega-mawile-metal — head-to-head 47/50 = 0.94. Ceiling caveat (recorded in the doc): the strong-side orientation has only +0.06 of headroom, so P1's information lives mostly in the weak-side orientation (can search rescue mawile?).
- **P2 strong-vs-mid:** mega-lucario-fighting vs mega-lucario-v3 — head-to-head 34/50 = 0.68. Headroom both sides.
- **P3 mid-vs-mid (cross-archetype):** mega-lucario-v4 vs mega-starmie-water — head-to-head 40/100 = 0.40 for v4 (0.60 starmie). Near-balanced, different archetypes (avoids re-testing the symmetric mirror).

**Cell scheme:** 12 cells = 3 pairings x 2 orientations x {treatment, control}. Orientation A = the pairing's first-listed deck is the pilot (agent-a / deck-a); orientation B = second-listed deck pilots. Treatment = `search-net` (v2 weights, operating config: 200 ms budget, defaults otherwise) pilots vs `heuristic`; control = `heuristic` vs `heuristic` on the identical deck assignment. 150 games/cell. The two control cells per pairing are statistically the same matchup viewed from each side; running both preserves cell symmetry and doubles control precision.

**Metric + decision (pre-registered):** per-cell differential = treatment pilot WR minus control pilot WR (same deck, same orientation). Pooled: sum pilot wins over the 6 treatment cells (n=900) minus the same over the 6 control cells (n=900); 95% CI via normal approximation `d +/- 1.96*sqrt(pt(1-pt)/nt + pc(1-pc)/nc)`. **POSITIVE** (CI lower bound > 0) -> search candidates seed at priority 0.90 (top of the factory queue) and 7B is armed with confidence. **NEGATIVE** (CI upper bound < 0) or **FLAT** (CI spans 0) -> search candidates seed at priority 0.30 flagged exploratory; the no-edge hypothesis strengthens. **Replication trigger:** if |CI lower bound| < 0.02 (or |upper| < 0.02 for a negative result), replicate all 12 cells once and pool per `.claude/rules/stochastic-gate-replication.md`; report every run.

**Row conventions:** notes = `slice7a-asym-<P#>-<A|B>-<T|C>`; a bad/aborted run's row gets `VOID` appended to its notes by hand and the parser skips it; re-runs of the same cell ACCUMULATE (pooled) by design. Pre-flight `--games 1` dry-runs use notes `slice7a-asym-dryrun` (no cell token, so the parser ignores them).

**Files:**

- Create: `src/ptcg/factory/__init__.py`
- Create: `src/ptcg/factory/asym.py`
- Create: `scripts/asym_report.py`
- Create: `experiments/ANALYSIS-slice7a-asymmetric-test.md` (pre-registration; results appended by T2)
- Test: `tests/test_factory_asym.py`

**Interfaces:**

- Consumes: `experiments/EXPERIMENTS.md` row shape (13 pipe-separated columns; wins_a = column 7, games = column 6, notes = column 13).
- Produces: `Cell(cell_id: str, wins: int, games: int)` with `.wr`; `diff_ci(wins_t, n_t, wins_c, n_c, z=1.96) -> tuple[float, tuple[float, float]]`; `compute(cells: dict[str, Cell]) -> AsymResult(pooled_treatment_wr, pooled_control_wr, differential, ci, per_cell, decision)`; `parse_experiments(md_text: str, prefix="slice7a-asym-") -> dict[str, Cell]`; `write_decision(path: Path, result: AsymResult) -> None` writing `experiments/factory/asym_decision.json` with keys `{"decision", "differential", "ci", "pooled_treatment_wr", "pooled_control_wr", "per_cell"}`. CLI `scripts/asym_report.py [--experiments PATH] [--write-decision PATH]` prints a markdown table and optionally writes the decision JSON.

**Steps:**

- [ ] Write the failing test `tests/test_factory_asym.py` (hand-verified vectors: `diff_ci(90,150,150,300)` -> d=0.1, CI [0.0033155, 0.1966845]; flat vector 150/300 vs 150/300 -> CI half-width 0.0800167):

```python
"""Tests for the pre-registered asymmetric-test differential computation."""
from __future__ import annotations

import json

import pytest

from ptcg.factory.asym import Cell, compute, diff_ci, parse_experiments, write_decision


def test_diff_ci_matches_hand_verified_vector():
    # Hand-verified 2026-07-11: pt=0.6 (90/150), pc=0.5 (150/300)
    # se = sqrt(.24/150 + .25/300) = 0.0493288; d=0.1; CI = [0.0033155, 0.1966845]
    d, (lo, hi) = diff_ci(90, 150, 150, 300)
    assert d == pytest.approx(0.1)
    assert lo == pytest.approx(0.0033155, abs=1e-5)
    assert hi == pytest.approx(0.1966845, abs=1e-5)


def test_compute_pools_cells_and_decides_positive():
    cells = {
        "P1-A-T": Cell("P1-A-T", 90, 150),
        "P1-A-C": Cell("P1-A-C", 75, 150),
        "P1-B-C": Cell("P1-B-C", 75, 150),
    }
    r = compute(cells)
    assert r.pooled_treatment_wr == pytest.approx(0.6)
    assert r.pooled_control_wr == pytest.approx(0.5)
    assert r.differential == pytest.approx(0.1)
    assert r.per_cell["P1-A"] == pytest.approx(0.1)
    assert r.decision == "positive"  # CI [0.0033, 0.1967] wholly above 0


def test_compute_flat_when_ci_spans_zero():
    # Hand-verified 2026-07-11: both sides 150/300 -> d=0, CI = +/-0.0800167
    cells = {
        "P1-A-T": Cell("P1-A-T", 75, 150),
        "P1-B-T": Cell("P1-B-T", 75, 150),
        "P1-A-C": Cell("P1-A-C", 75, 150),
        "P1-B-C": Cell("P1-B-C", 75, 150),
    }
    r = compute(cells)
    assert r.differential == pytest.approx(0.0)
    assert r.ci[1] == pytest.approx(0.0800167, abs=1e-5)
    assert r.decision == "flat"


ROW = ("| 2026-07-12 | search-net-v2 | heuristic-v0 | x.csv | y.csv | 150 | 90 | 60 | 0 "
       "| 0.600 [0.520, 0.675] | 2.10 | 0.210 | slice7a-asym-P1-A-T dev=8.0% |")
CTRL = ("| 2026-07-12 | heuristic-v0 | heuristic-v0 | x.csv | y.csv | 150 | 75 | 75 | 0 "
        "| 0.500 [0.421, 0.579] | 0.09 | 0.002 | slice7a-asym-P1-A-C |")
VOIDED = ("| 2026-07-12 | search-net-v2 | heuristic-v0 | x.csv | y.csv | 150 | 10 | 5 | 0 "
          "| 0.667 [0.417, 0.848] | 2.10 | 0.210 | slice7a-asym-P1-B-T VOID crashed |")
DRYRUN = ("| 2026-07-12 | search-net-v2 | heuristic-v0 | x.csv | y.csv | 1 | 1 | 0 | 0 "
          "| 1.000 [0.207, 1.000] | 2.10 | 0.210 | slice7a-asym-dryrun |")


def test_parse_experiments_extracts_cells_skips_void_and_dryrun():
    cells = parse_experiments("\n".join(["# header", ROW, CTRL, VOIDED, DRYRUN]))
    assert set(cells) == {"P1-A-T", "P1-A-C"}
    assert cells["P1-A-T"].wins == 90 and cells["P1-A-T"].games == 150


def test_parse_experiments_accumulates_replicated_cells():
    cells = parse_experiments("\n".join([ROW, ROW]))
    assert cells["P1-A-T"].wins == 180 and cells["P1-A-T"].games == 300


def test_write_decision_round_trips(tmp_path):
    r = compute({"P1-A-T": Cell("P1-A-T", 90, 150), "P1-A-C": Cell("P1-A-C", 75, 150),
                 "P1-B-C": Cell("P1-B-C", 75, 150)})
    out = tmp_path / "asym_decision.json"
    write_decision(out, r)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["decision"] == "positive"
    assert doc["differential"] == pytest.approx(0.1)
```

- [ ] Run to see it fail: `uv run pytest tests/test_factory_asym.py -q` — expected: `ModuleNotFoundError: No module named 'ptcg.factory'`.
- [ ] Create `src/ptcg/factory/__init__.py`:

```python
"""Agent factory: automated candidate production, evaluation, submission, harvest."""
```

- [ ] Create `src/ptcg/factory/asym.py`:

```python
"""Asymmetric-pairing discriminating test: pre-registered differential computation.

Design + decision criteria: experiments/ANALYSIS-slice7a-asymmetric-test.md (locked
BEFORE any runs). Cells come from experiments/EXPERIMENTS.md rows whose notes carry
`slice7a-asym-<P#>-<A|B>-<T|C>`. Rows containing VOID are skipped; replicated cells
accumulate (pooled) by design.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

CELL_RE = re.compile(r"(P\d-[AB]-[TC])\b")


@dataclass
class Cell:
    cell_id: str  # e.g. "P1-A-T"
    wins: int     # agent-a (pilot-side) wins
    games: int

    @property
    def wr(self) -> float:
        return self.wins / self.games if self.games else 0.0


@dataclass
class AsymResult:
    pooled_treatment_wr: float
    pooled_control_wr: float
    differential: float
    ci: tuple[float, float]
    per_cell: dict[str, float] = field(default_factory=dict)  # "P1-A" -> per-cell diff
    decision: str = "flat"  # "positive" | "flat" | "negative"


def diff_ci(wins_t: int, n_t: int, wins_c: int, n_c: int,
            z: float = 1.96) -> tuple[float, tuple[float, float]]:
    """Normal-approx CI for a difference of two independent proportions."""
    pt, pc = wins_t / n_t, wins_c / n_c
    se = math.sqrt(pt * (1 - pt) / n_t + pc * (1 - pc) / n_c)
    d = pt - pc
    return d, (d - z * se, d + z * se)


def compute(cells: dict[str, Cell]) -> AsymResult:
    treat = [c for cid, c in cells.items() if cid.endswith("-T")]
    ctrl = [c for cid, c in cells.items() if cid.endswith("-C")]
    if not treat or not ctrl:
        raise ValueError("need at least one treatment and one control cell")
    wins_t, n_t = sum(c.wins for c in treat), sum(c.games for c in treat)
    wins_c, n_c = sum(c.wins for c in ctrl), sum(c.games for c in ctrl)
    d, ci = diff_ci(wins_t, n_t, wins_c, n_c)
    per_cell: dict[str, float] = {}
    for cid, cell in cells.items():
        if cid.endswith("-T"):
            control = cells.get(cid[:-2] + "-C")
            if control is not None:
                per_cell[cid[:-2]] = cell.wr - control.wr
    if ci[0] > 0:
        decision = "positive"
    elif ci[1] < 0:
        decision = "negative"
    else:
        decision = "flat"
    return AsymResult(wins_t / n_t, wins_c / n_c, d, ci, per_cell, decision)


def parse_experiments(md_text: str, prefix: str = "slice7a-asym-") -> dict[str, Cell]:
    """Accumulate cells from EXPERIMENTS.md rows. Row shape (stats.markdown_row):
    | date | A | B | deckA | deckB | games | winsA | winsB | draws | wr [ci] | avg | max | notes |
    """
    cells: dict[str, Cell] = {}
    for line in md_text.splitlines():
        if not line.startswith("|") or prefix not in line or "VOID" in line:
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) < 13:
            continue
        m = CELL_RE.search(parts[12])
        if m is None or prefix + m.group(1) not in parts[12]:
            continue
        cell_id = m.group(1)
        wins, games = int(parts[6]), int(parts[5])
        if cell_id in cells:
            cells[cell_id] = Cell(cell_id, cells[cell_id].wins + wins,
                                  cells[cell_id].games + games)
        else:
            cells[cell_id] = Cell(cell_id, wins, games)
    return cells


def write_decision(path: Path, result: AsymResult) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "decision": result.decision,
        "differential": result.differential,
        "ci": list(result.ci),
        "pooled_treatment_wr": result.pooled_treatment_wr,
        "pooled_control_wr": result.pooled_control_wr,
        "per_cell": result.per_cell,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] Create `scripts/asym_report.py`:

```python
"""CLI: compute the pre-registered asymmetric-test differentials from EXPERIMENTS.md."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.factory.asym import compute, parse_experiments, write_decision  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--experiments", default=str(ROOT / "experiments" / "EXPERIMENTS.md"))
    p.add_argument("--write-decision", default=None,
                   help="path to write asym_decision.json (e.g. "
                        "experiments/factory/asym_decision.json)")
    args = p.parse_args()

    text = Path(args.experiments).read_text(encoding="utf-8")
    cells = parse_experiments(text)
    if not cells:
        raise SystemExit("no slice7a-asym cells found in EXPERIMENTS.md")
    result = compute(cells)

    print("| Cell | Pilot wins | Games | WR |")
    print("|------|-----------:|------:|----|")
    for cid in sorted(cells):
        c = cells[cid]
        print(f"| {cid} | {c.wins} | {c.games} | {c.wr:.3f} |")
    print()
    print("| Pairing-orientation | Differential (treatment - control) |")
    print("|---------------------|-------------------------------------|")
    for key in sorted(result.per_cell):
        print(f"| {key} | {result.per_cell[key]:+.3f} |")
    lo, hi = result.ci
    print()
    print(f"Pooled treatment WR: {result.pooled_treatment_wr:.4f}")
    print(f"Pooled control   WR: {result.pooled_control_wr:.4f}")
    print(f"Pooled differential: {result.differential:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]")
    print(f"DECISION (pre-registered criteria): {result.decision.upper()}")
    if args.write_decision:
        write_decision(Path(args.write_decision), result)
        print(f"decision written -> {args.write_decision}")


if __name__ == "__main__":
    main()
```

- [ ] Run to pass: `uv run pytest tests/test_factory_asym.py tests/test_experiments_append.py -q` — expected: all pass.
- [ ] Create `experiments/ANALYSIS-slice7a-asymmetric-test.md` with EXACTLY this pre-registration content (results section left as a stub T2 fills in; keep ASCII):

```markdown
# Slice 7A: Asymmetric-Pairing Discriminating Test

Status: PRE-REGISTERED (this section locked before any runs; see git history)
Spec: docs/superpowers/specs/2026-07-11-slice7a-agent-factory-design.md, Section 7

## Question

Does search (search-net, v2 weights, operating config) add win rate over heuristic-v0
when the DECKS are asymmetric, after stripping deck-strength effects with a v0-vs-v0
control on the identical deck assignment? Slices 4-6 established parity on the
symmetric mega-lucario mirror; this test discriminates "search has no per-decision
edge anywhere" from "the mirror specifically has no exploitable edge."

## Pairings (from experiments/tournament/results.json, Slice 3)

Pooled Slice-3 WRs: mega-lucario-fighting 411/550=0.747; mega-lucario-v4 338/500=0.676;
mega-starmie-water 439/650=0.675; mega-lucario-v3 450/850=0.529; mega-mawile-metal
168/400=0.420.

- P1 strong-vs-weak: mega-lucario-fighting vs mega-mawile-metal (h2h 47/50 = 0.94).
  Ceiling caveat: strong-side orientation has only +0.06 headroom; P1's information
  lives mostly in the weak-side orientation.
- P2 strong-vs-mid: mega-lucario-fighting vs mega-lucario-v3 (h2h 34/50 = 0.68).
- P3 mid-vs-mid, cross-archetype: mega-lucario-v4 vs mega-starmie-water
  (h2h 40/100 = 0.40 for v4).

## Design

12 cells: 3 pairings x 2 orientations x {treatment, control}, 150 games/cell.
Orientation A = first-listed deck pilots (agent-a/deck-a); B = second-listed pilots.
Treatment: search-net (value_net_weights_v2.json, 200 ms budget, run_arena defaults
otherwise) pilots vs heuristic-v0. Control: heuristic-v0 vs heuristic-v0, same decks.
The two control cells per pairing are the same matchup seen from each side; both run
anyway (cell symmetry + doubled control precision).

## Metric and decision criteria (pre-registered)

Per-cell differential = treatment pilot WR - control pilot WR (same deck/orientation).
Pooled differential = pooled treatment pilot WR (6 cells, n=900) - pooled control
pilot WR (6 cells, n=900); 95% CI = d +/- 1.96*sqrt(pt(1-pt)/nt + pc(1-pc)/nc).

- POSITIVE (CI lower bound > 0): search has an exploitable asymmetric edge. Search
  candidates seed the factory queue at priority 0.90 (top priority); the 7B
  policy-improvement trainer is armed with confidence.
- NEGATIVE (CI upper bound < 0) or FLAT (CI spans 0): no-edge hypothesis strengthens.
  Search candidates seed at priority 0.30 flagged exploratory (the real ladder still
  gets its say); 7B proceeds as the remaining untested hypothesis.
- Replication trigger: if the deciding CI bound is within 0.02 of zero, replicate all
  12 cells once and pool (rows accumulate by cell id), per
  .claude/rules/stochastic-gate-replication.md. Report every run.

Row conventions: notes = slice7a-asym-<P#>-<A|B>-<T|C>. A bad run's row gets VOID
appended by hand (parser skips it). Dry-runs use notes slice7a-asym-dryrun.
Analysis code: src/ptcg/factory/asym.py + scripts/asym_report.py (committed with this
pre-registration, before any data).

## Command matrix (12 cells)

Treatment cells (~2.1 s/game at 200 ms budget => ~5-6 min/cell; controls are seconds):

    # P1-A-T
    uv run python scripts/run_arena.py --agent-a search-net --agent-b heuristic \
      --deck-a src/ptcg/decks/candidates/mega-lucario-fighting.csv \
      --deck-b src/ptcg/decks/candidates/mega-mawile-metal.csv \
      --games 150 --search-budget-ms 200 \
      --net-weights src/ptcg/search/value_net_weights_v2.json \
      --collect-stats --notes "slice7a-asym-P1-A-T"
    # P1-B-T: swap --deck-a/--deck-b, notes slice7a-asym-P1-B-T
    # P1-A-C
    uv run python scripts/run_arena.py --agent-a heuristic --agent-b heuristic \
      --deck-a src/ptcg/decks/candidates/mega-lucario-fighting.csv \
      --deck-b src/ptcg/decks/candidates/mega-mawile-metal.csv \
      --games 150 --notes "slice7a-asym-P1-A-C"
    # P1-B-C: swap decks, notes slice7a-asym-P1-B-C
    # P2 cells: mega-lucario-fighting.csv / mega-lucario-v3.csv, notes P2-*
    # P3 cells: mega-lucario-v4.csv / mega-starmie-water.csv, notes P3-*

Report: uv run python scripts/asym_report.py --write-decision \
  experiments/factory/asym_decision.json

## Results (T2 appends below this line - empty at pre-registration)
```

- [ ] Run the full suite: `uv run pytest -q` — expected: green (no existing behavior touched).
- [ ] Commit:

```
git add src/ptcg/factory/__init__.py src/ptcg/factory/asym.py scripts/asym_report.py tests/test_factory_asym.py "experiments/ANALYSIS-slice7a-asymmetric-test.md"
git commit -m "feat: slice7a T1 - asymmetric-test pre-registration + differential module"
```

---

### Task 2: [ORCH] Execute asymmetric test + record decision

Structured per `.claude/rules/background-arena-execution.md`: the IMPLEMENTER only validates commands (setup); the ORCHESTRATOR launches all 12 series in its own background shells; a HARVEST agent computes differentials and writes the analysis. No agent waits on a running series.

**Files:**

- Modify: `experiments/ANALYSIS-slice7a-asymmetric-test.md` (results + decision appended)
- Create: `experiments/factory/asym_decision.json` (via `scripts/asym_report.py --write-decision`)
- Modify: `experiments/EXPERIMENTS.md` (12+ auto-appended rows)

**Interfaces:**

- Consumes: T1's command matrix, `scripts/asym_report.py`.
- Produces: `asym_decision.json` (consumed by T4 seeding and T12's trainer priority).

**Steps:**

- [ ] (implementer/setup) Dry-run ONE treatment and ONE control command with `--games 1 --notes "slice7a-asym-dryrun"`; confirm exit 0, a row appended, and the resolved flags match the T1 matrix. Report the validated command strings and STOP (do not launch full runs).
- [ ] (orchestrator) Launch the 6 treatment cells in background shells (sequential or max 2 concurrent), then the 6 control cells (controls take seconds). Watch PIDs via `Get-Process`; harvest by reading EXPERIMENTS.md after each exit. Expected wall time ~35 min total.
- [ ] (orchestrator) If any run dies mid-series, append `VOID` to its partial row's notes by hand and relaunch that cell.
- [ ] (harvest agent) Run `uv run python scripts/asym_report.py --write-decision experiments/factory/asym_decision.json`; verify the printed cell table against the raw EXPERIMENTS.md rows (wins/games per cell, all 12 present).
- [ ] (harvest agent) Check the replication trigger: if the deciding CI bound is within 0.02 of zero, report back — the orchestrator reruns all 12 cells and re-harvests (rows accumulate). Record ALL runs.
- [ ] (harvest agent) Append to `experiments/ANALYSIS-slice7a-asymmetric-test.md` under `## Results`: the cell table, per-cell differentials, pooled differential + CI, the DECISION line, run dates, and any VOIDed/replicated runs. Keep ASCII.
- [ ] (harvest agent) Commit:

```
git add "experiments/ANALYSIS-slice7a-asymmetric-test.md" experiments/factory/asym_decision.json experiments/EXPERIMENTS.md
git commit -m "docs: slice7a T2 - asymmetric-pairing test executed, decision recorded"
```

---

### Task 3: Candidate model + queue ledger

**Files:**

- Create: `src/ptcg/factory/candidates.py`
- Modify: `tests/test_experiments_append.py` (extend the utf-8 guard to all factory files)
- Test: `tests/test_factory_candidates.py`

**Interfaces:**

- Consumes: nothing (stdlib only).
- Produces (used verbatim by T4-T13):
  - `class Status(str, Enum)` with values `queued`, `evaluating`, `evaluated`, `evaluated-below-incumbent` (member name `BELOW_INCUMBENT`), `submitted`, `scored`, `retired`.
  - `@dataclass Candidate(id, name, version, deck, agent_kind, agent_config: dict, provenance: str, priority: float, status: Status, novel_axis: bool, local_wr: float | None, local_games: int, kaggle_score: float | None, submitted_at: str | None, score_history: list, notes: str)` + `Candidate.create(name=, version=, deck=, agent_kind=, **kw)` (derives `id`).
  - `parse_version("v1.2") -> (1, 2)`, `bump_minor("v1.2") -> "v1.3"`, `make_id(name, version) -> "name-v1.2"`, `next_version(candidates, name) -> str` (`"v0.1"` when name unseen, else minor+1 of the max).
  - `load_ledger(path: Path) -> list[Candidate]` (missing file -> `[]`; ignores unknown JSON keys for forward compat), `save_ledger(path: Path, candidates) -> None` (atomic temp+`os.replace`, `encoding="utf-8"`).

**Steps:**

- [ ] Write the failing test `tests/test_factory_candidates.py`:

```python
"""Tests for the factory candidate model + JSON ledger."""
from __future__ import annotations

import json

import pytest

from ptcg.factory.candidates import (Candidate, Status, bump_minor, load_ledger,
                                     make_id, next_version, parse_version, save_ledger)


def test_version_parse_and_bump():
    assert parse_version("v1.2") == (1, 2)
    assert bump_minor("v1.2") == "v1.3"
    assert make_id("lucario-heuristic", "v1.0") == "lucario-heuristic-v1.0"
    with pytest.raises(ValueError):
        parse_version("1.2")


def test_next_version_bumps_max_minor():
    cands = [
        Candidate.create(name="a-net", version="v0.1", deck="d.csv", agent_kind="heuristic"),
        Candidate.create(name="a-net", version="v0.3", deck="d.csv", agent_kind="heuristic"),
        Candidate.create(name="other", version="v9.9", deck="d.csv", agent_kind="heuristic"),
    ]
    assert next_version(cands, "a-net") == "v0.4"
    assert next_version(cands, "brand-new") == "v0.1"


def test_ledger_round_trip_atomic_and_utf8(tmp_path):
    path = tmp_path / "candidates.json"
    cand = Candidate.create(
        name="lucario-heuristic", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", provenance="seed", priority=0.747,
        notes="unicode ok: ⚠")
    cand.status = Status.EVALUATED
    cand.local_wr = 0.6
    save_ledger(path, [cand])
    assert not path.with_suffix(".json.tmp").exists()  # temp+rename cleaned up
    loaded = load_ledger(path)
    assert loaded[0].id == "lucario-heuristic-v1.0"
    assert loaded[0].status is Status.EVALUATED
    assert loaded[0].local_wr == 0.6
    assert loaded[0].notes.endswith("⚠")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["candidates"][0]["status"] == "evaluated"


def test_load_ledger_missing_file_and_unknown_keys(tmp_path):
    assert load_ledger(tmp_path / "nope.json") == []
    doc = {"version": 1, "candidates": [{
        "id": "x-v1.0", "name": "x", "version": "v1.0", "deck": "d.csv",
        "agent_kind": "heuristic", "status": "queued",
        "some_future_field": 42}]}
    p = tmp_path / "candidates.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    loaded = load_ledger(p)
    assert loaded[0].name == "x" and loaded[0].status is Status.QUEUED
```

- [ ] Run to see it fail: `uv run pytest tests/test_factory_candidates.py -q` — expected: `ModuleNotFoundError: No module named 'ptcg.factory.candidates'`.
- [ ] Create `src/ptcg/factory/candidates.py`:

```python
"""Candidate model + git-tracked JSON queue ledger (spec S2).

A candidate is a durable versioned identity: name + vMAJOR.MINOR mapping to an
exact deck, agent config, net weights file, and provenance, so every ladder
score is forever traceable to reproducible code.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    QUEUED = "queued"
    EVALUATING = "evaluating"
    EVALUATED = "evaluated"
    BELOW_INCUMBENT = "evaluated-below-incumbent"
    SUBMITTED = "submitted"
    SCORED = "scored"
    RETIRED = "retired"


def parse_version(version: str) -> tuple[int, int]:
    if not version.startswith("v"):
        raise ValueError(f"version must look like vMAJOR.MINOR, got {version!r}")
    major_s, _, minor_s = version[1:].partition(".")
    return int(major_s), int(minor_s)


def bump_minor(version: str) -> str:
    major, minor = parse_version(version)
    return f"v{major}.{minor + 1}"


def make_id(name: str, version: str) -> str:
    return f"{name}-{version}"


@dataclass
class Candidate:
    id: str
    name: str
    version: str
    deck: str                 # repo-relative deck csv path
    agent_kind: str           # "heuristic" | "search-net"
    agent_config: dict = field(default_factory=dict)
    provenance: str = "seed"
    priority: float = 0.5
    status: Status = Status.QUEUED
    novel_axis: bool = False  # exploration-exception eligibility (spec S4)
    local_wr: float | None = None
    local_games: int = 0
    kaggle_score: float | None = None
    submitted_at: str | None = None      # ISO datetime of upload
    score_history: list = field(default_factory=list)  # [[iso_ts, score], ...]
    notes: str = ""

    @classmethod
    def create(cls, *, name: str, version: str, deck: str, agent_kind: str,
               **kw) -> "Candidate":
        return cls(id=make_id(name, version), name=name, version=version,
                   deck=deck, agent_kind=agent_kind, **kw)


def next_version(candidates: list[Candidate], name: str) -> str:
    versions = [parse_version(c.version) for c in candidates if c.name == name]
    if not versions:
        return "v0.1"
    major, minor = max(versions)
    return f"v{major}.{minor + 1}"


def load_ledger(path: Path) -> list[Candidate]:
    path = Path(path)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Candidate)}
    out: list[Candidate] = []
    for row in payload.get("candidates", []):
        row = {k: v for k, v in row.items() if k in known}
        row["status"] = Status(row.get("status", "queued"))
        out.append(Candidate(**row))
    return out


def save_ledger(path: Path, candidates: list[Candidate]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "candidates": [{**asdict(c), "status": c.status.value} for c in candidates],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] Extend `tests/test_experiments_append.py` with a glob-based guard over ALL factory files (append at the end of the file — factory modules added later in the slice are covered automatically):

```python
def test_factory_files_pin_utf8_encoding():
    """Spec S9: every factory text write pins encoding="utf-8". Glob-based so
    files added later in the slice are covered without editing this test."""
    targets = sorted((ROOT / "src" / "ptcg" / "factory").glob("*.py"))
    targets += sorted(ROOT.glob("scripts/factory_*.py"))
    targets += sorted(ROOT.glob("scripts/seed_candidates.py"))
    targets += sorted(ROOT.glob("scripts/asym_report.py"))
    assert targets, "factory sources not found - test target may have moved"
    problems = {}
    for target in targets:
        missing = _call_sites_missing_encoding(target.read_text(encoding="utf-8"))
        if missing:
            problems[str(target)] = missing
    assert problems == {}, f"factory file(s) missing encoding= kwarg: {problems}"
```

- [ ] Run to pass: `uv run pytest tests/test_factory_candidates.py tests/test_experiments_append.py -q` — expected: all pass (including the new guard over `asym.py`/`candidates.py`).
- [ ] Commit:

```
git add src/ptcg/factory/candidates.py tests/test_factory_candidates.py tests/test_experiments_append.py
git commit -m "feat: slice7a T3 - candidate model, versioning, atomic JSON ledger + utf-8 guard extension"
```

---

### Task 4: Seed the initial candidate pool (combined-review eligible)

Priorities are COMPUTED from `experiments/tournament/results.json` (never hardcoded prose numbers) and from T2's recorded decision. Expected computed heuristic priorities (hand-verified 2026-07-11 from the ledger): mega-lucario-fighting 0.747, mega-lucario-v4 0.676, mega-starmie-water 0.675, mega-lucario-v2 0.577, mega-lucario-v3 0.529, mega-mawile-metal 0.420, disruption-darkness 0.268, bigbasic-dragon 0.220, aggro-lightning 0.040.

**Files:**

- Create: `scripts/seed_candidates.py`
- Create: `experiments/factory/candidates.json` (committed output)
- Test: `tests/test_seed_candidates.py`

**Interfaces:**

- Consumes: `experiments/tournament/results.json` (`{"decks": {hash: filename}, "pairings": [{deck_a, deck_b, wins_a, wins_b, games, ...}]}`), `experiments/factory/asym_decision.json` (T2), `ptcg.factory.candidates`.
- Produces: `tournament_priorities(results_path) -> dict[slug, float]`; `search_priority(decision_path) -> float` (0.90 if decision positive else 0.30); `build_seed(prios, search_prio) -> list[Candidate]` (9 heuristic + 4 search = 13); CLI writes/merges the ledger idempotently (existing ids never duplicated).

**Steps:**

- [ ] Write the failing test `tests/test_seed_candidates.py`:

```python
"""Tests for the initial candidate pool seeding."""
from __future__ import annotations

import json

from scripts.seed_candidates import build_seed, search_priority, tournament_priorities


def test_tournament_priorities_pools_wins(tmp_path):
    doc = {"version": 1,
           "decks": {"h1": "alpha.csv", "h2": "beta.csv"},
           "pairings": [{"deck_a": "h1", "deck_b": "h2", "agent": "heuristic-v0",
                         "wins_a": 30, "wins_b": 20, "draws": 0, "games": 50,
                         "discarded": 0}]}
    p = tmp_path / "results.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    prios = tournament_priorities(p)
    # Hand-verified 2026-07-11: alpha 30/50 = 0.6, beta 20/50 = 0.4
    assert prios == {"alpha": 0.6, "beta": 0.4}


def test_search_priority_reads_decision(tmp_path):
    p = tmp_path / "asym_decision.json"
    p.write_text(json.dumps({"decision": "positive"}), encoding="utf-8")
    assert search_priority(p) == 0.90
    p.write_text(json.dumps({"decision": "flat"}), encoding="utf-8")
    assert search_priority(p) == 0.30


def test_build_seed_shapes_the_pool():
    prios = {"mega-lucario-fighting": 0.747, "mega-starmie-water": 0.675,
             "mega-lucario-v4": 0.676, "aggro-lightning": 0.040}
    seeds = build_seed(prios, search_prio=0.30)
    ids = {c.id for c in seeds}
    # one heuristic candidate per deck
    assert "mega-lucario-fighting-heuristic-v1.0" in ids
    assert "aggro-lightning-heuristic-v1.0" in ids
    # search variants on the top decks + the root-prior variant
    assert "mega-lucario-fighting-searchnet-v1.0" in ids
    assert "mega-lucario-fighting-searchnet-prior-v1.0" in ids
    assert "mega-starmie-water-searchnet-v1.0" in ids
    assert "mega-lucario-v4-searchnet-v1.0" in ids
    by_id = {c.id: c for c in seeds}
    # the current ladder identity is NOT a novel axis; everything else is
    assert by_id["mega-lucario-fighting-heuristic-v1.0"].novel_axis is False
    assert by_id["aggro-lightning-heuristic-v1.0"].novel_axis is True
    sn = by_id["mega-lucario-fighting-searchnet-v1.0"]
    assert sn.priority == 0.30 and sn.novel_axis is True
    assert sn.agent_config["net_weights"] == "src/ptcg/search/value_net_weights_v2.json"
    prior = by_id["mega-lucario-fighting-searchnet-prior-v1.0"]
    assert prior.agent_config["use_root_prior"] is True
```

- [ ] Run to see it fail: `uv run pytest tests/test_seed_candidates.py -q` — expected: `ModuleNotFoundError: No module named 'scripts.seed_candidates'`.
- [ ] Create `scripts/seed_candidates.py`:

```python
"""Seed the initial ~13 factory candidates (spec S2): the 9 Slice-3 decks on
heuristic-v0 plus search-net variants on the top 3 decks and one root-PUCT-prior
config. Heuristic priorities = Slice-3 tournament pooled WRs (computed from the
ledger, never hardcoded); search priorities = the T2 asymmetric-test decision."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.factory.candidates import Candidate, load_ledger, save_ledger  # noqa: E402

DECK_DIR = "src/ptcg/decks/candidates"
SEARCH_TOP_DECKS = ("mega-lucario-fighting", "mega-lucario-v4", "mega-starmie-water")
V2_WEIGHTS = "src/ptcg/search/value_net_weights_v2.json"
CURRENT_LADDER_SLUG = "mega-lucario-fighting"  # existing identity: not a novel axis
SEARCH_CONFIG = {"search_budget_ms": 200, "rollout_depth": 12,
                 "deviate_min_visits": 20, "deviate_value_edge": 0.12,
                 "net_weights": V2_WEIGHTS}


def tournament_priorities(results_path: Path) -> dict[str, float]:
    doc = json.loads(Path(results_path).read_text(encoding="utf-8"))
    decks = doc["decks"]
    wins: dict[str, int] = {}
    games: dict[str, int] = {}
    for p in doc["pairings"]:
        for side, w in (("deck_a", p["wins_a"]), ("deck_b", p["wins_b"])):
            key = decks[p[side]]
            wins[key] = wins.get(key, 0) + w
            games[key] = games.get(key, 0) + p["games"]
    return {Path(k).stem: wins[k] / games[k] for k in wins}


def search_priority(decision_path: Path) -> float:
    doc = json.loads(Path(decision_path).read_text(encoding="utf-8"))
    return 0.90 if doc["decision"] == "positive" else 0.30


def build_seed(prios: dict[str, float], search_prio: float) -> list[Candidate]:
    seeds: list[Candidate] = []
    for slug, p in sorted(prios.items(), key=lambda kv: -kv[1]):
        seeds.append(Candidate.create(
            name=f"{slug}-heuristic", version="v1.0",
            deck=f"{DECK_DIR}/{slug}.csv", agent_kind="heuristic",
            provenance="seed:slice3-tournament", priority=round(p, 3),
            novel_axis=slug != CURRENT_LADDER_SLUG))
    for slug in SEARCH_TOP_DECKS:
        if slug not in prios:
            continue
        seeds.append(Candidate.create(
            name=f"{slug}-searchnet", version="v1.0",
            deck=f"{DECK_DIR}/{slug}.csv", agent_kind="search-net",
            agent_config=dict(SEARCH_CONFIG),
            provenance="seed:slice6-operating-config", priority=search_prio,
            novel_axis=True))
    if CURRENT_LADDER_SLUG in prios:
        seeds.append(Candidate.create(
            name=f"{CURRENT_LADDER_SLUG}-searchnet-prior", version="v1.0",
            deck=f"{DECK_DIR}/{CURRENT_LADDER_SLUG}.csv", agent_kind="search-net",
            agent_config={**SEARCH_CONFIG, "use_root_prior": True,
                          "prior_tau": 0.1, "c_puct": 1.5},
            provenance="seed:slice6-stage2-config", priority=search_prio,
            novel_axis=True))
    return seeds


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--results", default=str(ROOT / "experiments/tournament/results.json"))
    p.add_argument("--decision",
                   default=str(ROOT / "experiments/factory/asym_decision.json"))
    p.add_argument("--ledger",
                   default=str(ROOT / "experiments/factory/candidates.json"))
    args = p.parse_args()

    decision = Path(args.decision)
    if not decision.exists():
        raise SystemExit(f"{decision} missing - run the T2 asymmetric test first "
                         "(scripts/asym_report.py --write-decision)")
    seeds = build_seed(tournament_priorities(Path(args.results)),
                       search_priority(decision))
    ledger_path = Path(args.ledger)
    existing = load_ledger(ledger_path)
    existing_ids = {c.id for c in existing}
    added = [c for c in seeds if c.id not in existing_ids]
    save_ledger(ledger_path, existing + added)
    print(f"seeded {len(added)} new candidates ({len(existing)} already present) "
          f"-> {ledger_path}")
    for c in added:
        print(f"  {c.id:44s} priority={c.priority:.3f} novel_axis={c.novel_axis}")


if __name__ == "__main__":
    main()
```

- [ ] Run to pass: `uv run pytest tests/test_seed_candidates.py tests/test_experiments_append.py -q` — expected: all pass.
- [ ] Run for real: `uv run python scripts/seed_candidates.py` — expected: `seeded 13 new candidates` with priorities matching the hand-verified list above (0.747 down to 0.040) and search priority matching T2's decision. Re-run once to confirm idempotency (`seeded 0 new candidates`).
- [ ] Commit:

```
git add scripts/seed_candidates.py tests/test_seed_candidates.py experiments/factory/candidates.json
git commit -m "feat: slice7a T4 - seed 13 initial candidates, priorities from slice-3 ledger + asym decision"
```

---

### Task 5: Evaluation runner (sanity filter, not gatekeeper)

Spec S3: top-priority queued candidates run arena series vs the fixed baseline (heuristic-v0 + mega-lucario-fighting) within a wall-clock budget; ~45% floor; per-candidate crash isolation; partial results persist; queue resumes. Unit tests use a stubbed series function — NO real engine runs.

**Files:**

- Create: `src/ptcg/factory/evaluate.py`
- Test: `tests/test_factory_evaluate.py`

**Interfaces:**

- Consumes: `ptcg.factory.candidates` (`Candidate`, `Status`, `save_ledger`), `ptcg.arena.runner.load_deck/run_series`, `ptcg.arena.stats.SeriesStats`, `ptcg.agents.heuristic.HeuristicAgent`, `ptcg.agents.search_agent.SearchAgent`, `ptcg.search.searcher.SearchConfig`, `ptcg.search.timing.TimeManager`, `ptcg.search.value_net.ValueNetEvaluator`.
- Produces:
  - `@dataclass EvalConfig(games: int = 150, floor: float = 0.45, budget_minutes: float = 240.0, max_candidates: int | None = None, baseline_deck: Path = <mega-lucario-fighting.csv>, experiments_md: Path = <experiments/EXPERIMENTS.md>)`
  - `build_agent(candidate: Candidate, deck: list[int]) -> Agent` (maps `agent_config` -> `SearchConfig` for `search-net`; sets `agent.name = candidate.id`)
  - `evaluate_queued(candidates, cfg, ledger_path, save_fn, series_fn=_real_series, clock=time.monotonic, append_row=None) -> list[Candidate]`
  - `append_experiments_row(cand, stats, experiments_md, date=None)` (13-column `markdown_row`, notes `slice7a-factory-eval <id>`; creates the file with a header if missing so tests/tmp roots work)

**Steps:**

- [ ] Write the failing test `tests/test_factory_evaluate.py` (vectors hand-verified: 90/150 = 0.6 passes the 0.45 floor; 60/150 = 0.4 fails it):

```python
"""Tests for the factory evaluation runner (stubbed arena - no engine runs)."""
from __future__ import annotations

from ptcg.arena.stats import SeriesStats
from ptcg.factory.candidates import Candidate, Status, load_ledger, save_ledger
from ptcg.factory.evaluate import EvalConfig, append_experiments_row, evaluate_queued


def _cand(name: str, priority: float, **kw) -> Candidate:
    return Candidate.create(
        name=name, version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", priority=priority, **kw)


def _stats(wins_a: int, wins_b: int) -> SeriesStats:
    return SeriesStats(wins_a=wins_a, wins_b=wins_b,
                       game_seconds=[0.1] * (wins_a + wins_b))


def test_floor_and_pass_transitions_persist(tmp_path):
    cands = [_cand("good", 0.9), _cand("lemon", 0.5)]
    results = {"good-v1.0": _stats(90, 60), "lemon-v1.0": _stats(60, 90)}  # 0.6 / 0.4

    def series(c, cfg):
        return results[c.id]

    ledger = tmp_path / "candidates.json"
    done = evaluate_queued(cands, EvalConfig(), ledger, save_ledger, series_fn=series)
    assert [c.id for c in done] == ["good-v1.0", "lemon-v1.0"]  # priority order
    assert cands[0].status is Status.EVALUATED and cands[0].local_wr == 0.6
    assert cands[1].status is Status.RETIRED  # 0.4 < 0.45 floor: lemon never ladders
    assert load_ledger(ledger)[0].local_wr == 0.6  # persisted after each candidate


def test_crash_isolation_resumes_queue(tmp_path):
    cands = [_cand("boom", 0.9), _cand("ok", 0.5)]

    def series(c, cfg):
        if c.name == "boom":
            raise RuntimeError("engine crashed")
        return _stats(90, 60)

    evaluate_queued(cands, EvalConfig(), tmp_path / "l.json", save_ledger,
                    series_fn=series)
    assert cands[0].status is Status.QUEUED and "eval-error" in cands[0].notes
    assert cands[1].status is Status.EVALUATED  # queue resumed past the crash


def test_budget_and_max_candidates_stop_early(tmp_path):
    cands = [_cand("first", 0.9), _cand("second", 0.5)]
    t = {"now": 0.0}

    def series(c, cfg):
        t["now"] += 3600.0  # each series "takes an hour"
        return _stats(90, 60)

    evaluate_queued(cands, EvalConfig(budget_minutes=30.0), tmp_path / "l.json",
                    save_ledger, series_fn=series, clock=lambda: t["now"])
    assert cands[0].status is Status.EVALUATED
    assert cands[1].status is Status.QUEUED  # budget exhausted before second

    cands2 = [_cand("a", 0.9), _cand("b", 0.5)]
    evaluate_queued(cands2, EvalConfig(max_candidates=1), tmp_path / "l2.json",
                    save_ledger, series_fn=lambda c, cfg: _stats(90, 60))
    assert cands2[0].status is Status.EVALUATED and cands2[1].status is Status.QUEUED


def test_append_experiments_row_creates_file_and_appends(tmp_path):
    md = tmp_path / "EXPERIMENTS.md"
    cand = _cand("good", 0.9)
    cand.local_wr = 0.6
    append_experiments_row(cand, _stats(90, 60), md, date="2026-07-12")
    text = md.read_text(encoding="utf-8")
    assert "slice7a-factory-eval good-v1.0" in text
    assert "| 2026-07-12 | good-v1.0 | heuristic-v0 |" in text
```

- [ ] Run to see it fail: `uv run pytest tests/test_factory_evaluate.py -q` — expected: `ModuleNotFoundError: No module named 'ptcg.factory.evaluate'`.
- [ ] Create `src/ptcg/factory/evaluate.py`:

```python
"""Local evaluation runner: sanity filter, not gatekeeper (spec S3).

Runs top-priority queued candidates vs the fixed baseline (heuristic-v0 on
mega-lucario-fighting) inside a wall-clock budget. Per-candidate crash
isolation: a failed series marks the candidate back to queued with an error
note and the queue continues; the ledger is saved after every transition so
partial results always persist.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ptcg.agents.heuristic import HeuristicAgent
from ptcg.factory.candidates import Candidate, Status

ROOT = Path(__file__).resolve().parents[3]
BASELINE_DECK = ROOT / "src" / "ptcg" / "decks" / "candidates" / "mega-lucario-fighting.csv"

EXPERIMENTS_HEADER = (
    "# Experiment Log - PTCG AI Battle Challenge\n\n"
    "| Date | Agent A | Agent B | Deck A | Deck B | Games | A wins | B wins | Draws "
    "| A win-rate [95% CI] | Avg game s | Max move s | Notes |\n"
    "|------|---------|---------|--------|--------|-------|--------|--------|-------"
    "|---------------------|------------|------------|-------|\n"
)


@dataclass
class EvalConfig:
    games: int = 150
    floor: float = 0.45
    budget_minutes: float = 240.0
    max_candidates: int | None = None
    baseline_deck: Path = field(default_factory=lambda: BASELINE_DECK)
    experiments_md: Path = field(default_factory=lambda: ROOT / "experiments" / "EXPERIMENTS.md")


def build_agent(candidate: Candidate, deck: list[int]):
    """Construct the candidate's agent. Mirrors scripts/run_arena.py's AGENTS map."""
    if candidate.agent_kind == "heuristic":
        agent = HeuristicAgent()
    elif candidate.agent_kind == "search-net":
        from ptcg.agents.search_agent import SearchAgent
        from ptcg.search.searcher import SearchConfig
        from ptcg.search.timing import TimeManager
        from ptcg.search.value_net import ValueNetEvaluator
        cfg = candidate.agent_config
        sc = SearchConfig(
            rollout_depth=int(cfg.get("rollout_depth", 12)),
            deviate_min_visits=int(cfg.get("deviate_min_visits", 20)),
            deviate_value_edge=float(cfg.get("deviate_value_edge", 0.12)),
            final_move_rule=str(cfg.get("final_move_rule", "most_visited")),
            use_root_prior=bool(cfg.get("use_root_prior", False)),
            prior_tau=float(cfg.get("prior_tau", 0.1)),
            c_puct=float(cfg.get("c_puct", 1.5)),
        )
        tm = TimeManager(total_s=1e9,
                         max_move_s=float(cfg.get("search_budget_ms", 200)) / 1000.0)
        agent = SearchAgent(deck, config=sc, time_manager=tm,
                            evaluator=ValueNetEvaluator.load(ROOT / cfg["net_weights"]))
    else:
        raise ValueError(f"unknown agent_kind {candidate.agent_kind!r}")
    agent.name = candidate.id
    return agent


def _real_series(candidate: Candidate, cfg: EvalConfig):
    from ptcg.arena.runner import load_deck, run_series
    deck_a = load_deck(ROOT / candidate.deck)
    deck_b = load_deck(cfg.baseline_deck)
    return run_series(build_agent(candidate, deck_a), HeuristicAgent(),
                      deck_a, deck_b, cfg.games)


def append_experiments_row(cand: Candidate, stats, experiments_md: Path,
                           date: str | None = None) -> None:
    experiments_md = Path(experiments_md)
    row = stats.markdown_row(
        date or dt.date.today().isoformat(), cand.id, "heuristic-v0",
        Path(cand.deck).name, BASELINE_DECK.name,
        f"slice7a-factory-eval {cand.id}")
    if experiments_md.exists():
        text = experiments_md.read_text(encoding="utf-8")
    else:
        experiments_md.parent.mkdir(parents=True, exist_ok=True)
        text = EXPERIMENTS_HEADER
    experiments_md.write_text(text + row + "\n", encoding="utf-8")


def evaluate_queued(candidates: list[Candidate], cfg: EvalConfig, ledger_path: Path,
                    save_fn: Callable, series_fn: Callable = _real_series,
                    clock: Callable[[], float] = time.monotonic,
                    append_row: Callable | None = None) -> list[Candidate]:
    """Evaluate top-priority queued candidates until the budget/count runs out."""
    deadline = clock() + cfg.budget_minutes * 60.0
    queue = sorted((c for c in candidates if c.status is Status.QUEUED),
                   key=lambda c: -c.priority)
    done: list[Candidate] = []
    for cand in queue:
        if clock() >= deadline:
            break
        if cfg.max_candidates is not None and len(done) >= cfg.max_candidates:
            break
        cand.status = Status.EVALUATING
        save_fn(ledger_path, candidates)
        try:
            stats = series_fn(cand, cfg)
        except Exception as exc:  # crash isolation (spec S9): persist, resume queue
            cand.status = Status.QUEUED
            cand.notes = f"eval-error: {exc!r}"[:300]
            save_fn(ledger_path, candidates)
            continue
        cand.local_wr = stats.win_rate_a
        cand.local_games = stats.n
        if stats.win_rate_a < cfg.floor:
            cand.status = Status.RETIRED
            cand.notes = f"below {cfg.floor:.2f} floor vs baseline"
        else:
            cand.status = Status.EVALUATED
        save_fn(ledger_path, candidates)
        if append_row is not None:
            append_row(cand, stats)
        done.append(cand)
    return done
```

- [ ] Run to pass: `uv run pytest tests/test_factory_evaluate.py tests/test_experiments_append.py -q` — expected: all pass.
- [ ] Sanity-run `build_agent` against the real engine once (this is the "first production-scale run counts as a test" rung for the config mapping — one game, not a series): `uv run python -c "import sys; sys.path.insert(0,'src'); from ptcg.factory.candidates import Candidate; from ptcg.factory.evaluate import build_agent, EvalConfig, _real_series; c=Candidate.create(name='probe-searchnet', version='v0.1', deck='src/ptcg/decks/candidates/mega-lucario-fighting.csv', agent_kind='search-net', agent_config={'search_budget_ms':50,'net_weights':'src/ptcg/search/value_net_weights_v2.json'}); s=_real_series(c, EvalConfig(games=1)); print('OK', s.wins_a, s.wins_b)"` — expected: `OK` and a 1-0 or 0-1 result, no exception.
- [ ] Commit:

```
git add src/ptcg/factory/evaluate.py tests/test_factory_evaluate.py
git commit -m "feat: slice7a T5 - evaluation runner with floor, budget, crash isolation"
```

---

### Task 6: Parametrized bundle builder

A `Candidate` becomes a Kaggle bundle. Heuristic candidates delegate 100% to the existing `build_bundle` (golden test: byte-identical member set to today's pipeline). Search-net candidates additionally get the stdlib-pure search modules + weights JSON and a factory-generated `ptcg/agents/current.py` in the STAGED copy only (`main.py` and the repo's `current.py` are untouched). Every bundle can be scanned for torch/numpy imports.

**Files:**

- Create: `src/ptcg/factory/bundles.py`
- Test: `tests/test_factory_bundles.py`

**Interfaces:**

- Consumes: `scripts.package_submission.build_bundle/verify_bundle/smoke_bundle` (`scripts/` is an importable package; `pythonpath` includes the repo root for tests, and factory CLIs insert ROOT into `sys.path`), `ptcg.factory.candidates.Candidate`.
- Produces:
  - `SEARCH_MODULES: list[str]` (repo-relative paths of the 9 stdlib-pure search files)
  - `generate_current_py(candidate) -> str` (search-net `current.py` source; heuristic candidates never call this)
  - `build_candidate_bundle(candidate, out_dir: Path) -> Path` (returns tar path; staging at `out_dir/submission`)
  - `bundle_import_violations(tar_path) -> list[str]` (torch/numpy import scan over every `.py` member)
  - `verify_candidate_bundle(candidate, tar_path, staging_dir) -> None` (raises on `verify_bundle` problems, import violations, or smoke failure)

**Steps:**

- [ ] Write the failing test `tests/test_factory_bundles.py`:

```python
"""Tests for the parametrized candidate bundle builder."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from ptcg.factory.bundles import (SEARCH_MODULES, build_candidate_bundle,
                                  bundle_import_violations, generate_current_py)
from ptcg.factory.candidates import Candidate
from scripts.package_submission import build_bundle, smoke_bundle, verify_bundle

ROOT = Path(__file__).resolve().parents[1]
DECK = "src/ptcg/decks/candidates/mega-lucario-fighting.csv"


def _heuristic_cand() -> Candidate:
    return Candidate.create(name="mega-lucario-fighting-heuristic", version="v1.0",
                            deck=DECK, agent_kind="heuristic")


def _search_cand() -> Candidate:
    return Candidate.create(
        name="mega-lucario-fighting-searchnet", version="v1.0", deck=DECK,
        agent_kind="search-net",
        agent_config={"search_budget_ms": 200, "rollout_depth": 12,
                      "use_root_prior": True, "prior_tau": 0.05, "c_puct": 1.5,
                      "net_weights": "src/ptcg/search/value_net_weights_v2.json"})


def test_generated_current_py_compiles_and_pins_config():
    src = generate_current_py(_search_cand())
    compile(src, "current.py", "exec")  # syntactically valid
    assert "prior_tau=0.05" in src
    assert "use_root_prior=True" in src
    assert "value_net_weights_v2.json" in src
    assert "max_move_s=0.2" in src
    assert "total_s=480.0" in src  # per-match clock safety margin under Kaggle's 600 s


def _tar_with(py_source: str, tmp_path: Path) -> Path:
    tar_path = tmp_path / "t.tar.gz"
    data = py_source.encode("utf-8")
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo("mod.py")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return tar_path


def test_import_scan_flags_torch_and_numpy(tmp_path):
    assert bundle_import_violations(_tar_with("import torch\n", tmp_path))
    assert bundle_import_violations(_tar_with("from numpy import array\n", tmp_path))
    assert bundle_import_violations(_tar_with("import json\n", tmp_path)) == []


def test_heuristic_candidate_bundle_matches_baseline(tmp_path):
    baseline = build_bundle(ROOT / DECK, tmp_path / "a")
    cand_tar = build_candidate_bundle(_heuristic_cand(), tmp_path / "b")
    with tarfile.open(baseline) as ta, tarfile.open(cand_tar) as tb:
        assert sorted(ta.getnames()) == sorted(tb.getnames())
        for member in ("main.py", "deck.csv", "ptcg/agents/current.py"):
            assert ta.extractfile(member).read() == tb.extractfile(member).read()
    assert verify_bundle(cand_tar) == []
    assert bundle_import_violations(cand_tar) == []


def test_search_bundle_structure(tmp_path):
    tar = build_candidate_bundle(_search_cand(), tmp_path)
    with tarfile.open(tar) as tf:
        names = set(tf.getnames())
        for mod in SEARCH_MODULES:
            assert Path(mod).relative_to("src").as_posix() in names
        assert "ptcg/search/value_net_weights_v2.json" in names
        current = tf.extractfile("ptcg/agents/current.py").read().decode("utf-8")
    assert "SearchAgent" in current and "prior_tau=0.05" in current
    assert verify_bundle(tar) == []
    assert bundle_import_violations(tar) == []


@pytest.mark.slow
def test_search_bundle_smokes(tmp_path):
    build_candidate_bundle(_search_cand(), tmp_path)
    smoke_bundle(tmp_path / "submission")  # raises SystemExit on failure
```

- [ ] Run to see it fail: `uv run pytest tests/test_factory_bundles.py -q` — expected: `ModuleNotFoundError: No module named 'ptcg.factory.bundles'`.
- [ ] Create `src/ptcg/factory/bundles.py`:

```python
"""Parametrized Kaggle bundle builder: Candidate -> submission.tar.gz (spec S1/S9).

Reuses scripts/package_submission.py wholesale. Heuristic candidates produce a
bundle IDENTICAL to the current pipeline. Search-net candidates get the
stdlib-pure search modules + weights JSON and a generated ptcg/agents/current.py
written into the STAGED copy only - main.py and the repo current.py never change.
"""
from __future__ import annotations

import shutil
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:  # scripts/ is a package at the repo root
    sys.path.insert(0, str(ROOT))

from scripts.package_submission import build_bundle, smoke_bundle, verify_bundle  # noqa: E402

from ptcg.factory.candidates import Candidate  # noqa: E402

SEARCH_MODULES = [
    "src/ptcg/agents/search_agent.py",
    "src/ptcg/search/__init__.py",
    "src/ptcg/search/belief.py",
    "src/ptcg/search/evaluate.py",
    "src/ptcg/search/features.py",
    "src/ptcg/search/searcher.py",
    "src/ptcg/search/timing.py",
    "src/ptcg/search/tree.py",
    "src/ptcg/search/value_net.py",
]

# 480 s think-time bank: conservative margin under Kaggle's 600 s per-episode budget.
SEARCH_CURRENT_TEMPLATE = '''"""Factory-generated ladder identity for {candidate_id}. Do not edit."""
from pathlib import Path

from ptcg.agents.base import Agent
from ptcg.agents.search_agent import SearchAgent
from ptcg.search.searcher import SearchConfig
from ptcg.search.timing import TimeManager
from ptcg.search.value_net import ValueNetEvaluator

CURRENT_AGENT_NAME = "{candidate_id}"
CURRENT_DECK_PATH = Path("deck.csv")


def make_current_agent(deck: list) -> Agent:
    cfg = SearchConfig(rollout_depth={rollout_depth},
                       deviate_min_visits={deviate_min_visits},
                       deviate_value_edge={deviate_value_edge},
                       final_move_rule="{final_move_rule}",
                       use_root_prior={use_root_prior},
                       prior_tau={prior_tau},
                       c_puct={c_puct})
    weights = Path(__file__).resolve().parents[1] / "search" / "{weights_name}"
    agent = SearchAgent(deck, config=cfg,
                        time_manager=TimeManager(total_s=480.0,
                                                 max_move_s={max_move_s}),
                        evaluator=ValueNetEvaluator.load(weights))
    agent.name = "{candidate_id}"
    return agent
'''


def generate_current_py(candidate: Candidate) -> str:
    cfg = candidate.agent_config
    return SEARCH_CURRENT_TEMPLATE.format(
        candidate_id=candidate.id,
        rollout_depth=int(cfg.get("rollout_depth", 12)),
        deviate_min_visits=int(cfg.get("deviate_min_visits", 20)),
        deviate_value_edge=float(cfg.get("deviate_value_edge", 0.12)),
        final_move_rule=str(cfg.get("final_move_rule", "most_visited")),
        use_root_prior=bool(cfg.get("use_root_prior", False)),
        prior_tau=float(cfg.get("prior_tau", 0.1)),
        c_puct=float(cfg.get("c_puct", 1.5)),
        weights_name=Path(cfg["net_weights"]).name,
        max_move_s=float(cfg.get("search_budget_ms", 200)) / 1000.0,
    )


def bundle_import_violations(tar_path: Path) -> list[str]:
    """Scan every .py member for torch/numpy imports (serve-time is pure stdlib)."""
    bad: list[str] = []
    with tarfile.open(tar_path) as tf:
        for member in tf.getmembers():
            if not member.name.endswith(".py"):
                continue
            source = tf.extractfile(member).read().decode("utf-8")
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import torch", "from torch",
                                        "import numpy", "from numpy")):
                    bad.append(f"{member.name}: {stripped}")
    return bad


def build_candidate_bundle(candidate: Candidate, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    tar_path = build_bundle(ROOT / candidate.deck, out_dir)
    if candidate.agent_kind == "heuristic":
        return tar_path  # byte-identical to the existing pipeline
    if candidate.agent_kind != "search-net":
        raise ValueError(f"unknown agent_kind {candidate.agent_kind!r}")
    staging = out_dir / "submission"
    (staging / "ptcg" / "agents" / "current.py").write_text(
        generate_current_py(candidate), encoding="utf-8")
    for mod in SEARCH_MODULES:
        dst = staging / Path(mod).relative_to("src")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / mod, dst)
    weights_src = ROOT / candidate.agent_config["net_weights"]
    shutil.copy(weights_src, staging / "ptcg" / "search" / weights_src.name)
    with tarfile.open(tar_path, "w:gz") as tf:
        for item in sorted(staging.iterdir()):
            tf.add(item, arcname=item.name)
    return tar_path


def verify_candidate_bundle(candidate: Candidate, tar_path: Path,
                            staging_dir: Path) -> None:
    """Every auto-built bundle passes verify + import scan + full-battle smoke
    before upload (spec S9). Raises RuntimeError/SystemExit on any failure."""
    problems = verify_bundle(tar_path)
    problems += bundle_import_violations(tar_path)
    if problems:
        raise RuntimeError(f"bundle verification failed for {candidate.id}: "
                           + "; ".join(problems))
    smoke_bundle(staging_dir)
```

- [ ] Run to pass: `uv run pytest tests/test_factory_bundles.py tests/test_packaging.py tests/test_experiments_append.py -q` — expected: all pass (baseline packaging tests still green).
- [ ] Run the slow smoke once (search bundle plays a real full battle; SearchAgent's v0 fallback keeps it robust): `uv run pytest tests/test_factory_bundles.py -m slow -q` — expected: 1 passed. If the both-seats smoke exceeds the 120 s subprocess timeout, report `plan-drift` with the measured time (do not silently raise the timeout).
- [ ] Commit:

```
git add src/ptcg/factory/bundles.py tests/test_factory_bundles.py
git commit -m "feat: slice7a T6 - parametrized bundle builder with golden heuristic parity + no-torch scan"
```

---

### Task 7: Kaggle client + harvester (with CLI investigation step)

Spec S5: harvest runs at the START of every cycle. This task begins with a REAL-CLI INVESTIGATION so the parser is coded against observed output, not guessed flags.

**Files:**

- Create: `src/ptcg/factory/kaggle_client.py`
- Create: `src/ptcg/factory/harvest.py`
- Modify: `experiments/ANALYSIS-slice7a-asymmetric-test.md` (append `## Appendix: Kaggle CLI / episode-data investigation (T7)`)
- Test: `tests/test_factory_kaggle_client.py`, `tests/test_factory_harvest.py`

**Interfaces:**

- Consumes: `uvx kaggle` CLI (auth already cached in `~/.kaggle`, account bscode), `ptcg.factory.candidates`.
- Produces:
  - `@dataclass SubmissionRow(file_name, date, description, status, public_score: float | None)`
  - `KaggleClient(competition="pokemon-tcg-ai-battle", runner=subprocess.run)` with `list_submissions() -> list[SubmissionRow]` (via `competitions submissions -c <comp> --csv`) and `submit(bundle: Path, description: str) -> None` (via `competitions submit -c <comp> -f <bundle> -m <description>`); `parse_submissions_csv(text) -> list[SubmissionRow]` tolerant of camelCase/snake_case headers and `None`/empty scores.
  - `FakeKaggleClient(rows=None)` — same duck type; `submit` records to `.submitted` and prepends a PENDING row.
  - `harvest(client, candidates, ladder_path, today=None) -> HarvestResult(matched, scored_updates, submissions_today, unmatched_descriptions)` — matches rows to candidates by `description.startswith(f"{name} {version}")`, updates `kaggle_score`/`score_history`, promotes SUBMITTED->SCORED, rewrites `experiments/LADDER.md` idempotently (full regeneration, atomic write).
  - `reprioritize(candidates) -> int` — the simple, non-bandit hook (spec S10): if a name's latest scored version improved over its previous scored version, queued same-name candidates get +0.05 priority, else -0.05, clamped to [0, 1].

**Steps:**

- [ ] INVESTIGATION (do this FIRST; record findings before coding the parser):
  - Run `uvx kaggle competitions submissions -c pokemon-tcg-ai-battle --csv` and capture the exact header row + one data row (redact nothing — scores are public).
  - Run `uvx kaggle competitions leaderboard -c pokemon-tcg-ai-battle --show --csv | head -5` (or `-s` per `--help`) to check what leaderboard data the CLI exposes.
  - Probe episode/match data availability: check `uvx kaggle competitions files -c pokemon-tcg-ai-battle`, the competition page's data tab, and whether a Meta Kaggle-style episodes dataset exists for this competition. The question on record (spec S5): are opponent decks / move logs from real ladder games retrievable?
  - Append findings verbatim (headers observed, flags that worked, episode-data verdict) to `experiments/ANALYSIS-slice7a-asymmetric-test.md` under `## Appendix: Kaggle CLI / episode-data investigation (T7)`. If the real CSV headers differ from `fileName,date,description,status,publicScore`, adjust `parse_submissions_csv`'s key list AND the test fixture to the OBSERVED headers before proceeding.
- [ ] Write the failing tests `tests/test_factory_kaggle_client.py`:

```python
"""Tests for the Kaggle CLI wrapper (parser + fake client). The CSV fixture
mirrors headers observed from the real CLI in the T7 investigation step."""
from __future__ import annotations

from pathlib import Path

import pytest

from ptcg.factory.kaggle_client import (FakeKaggleClient, KaggleClient,
                                        SubmissionRow, parse_submissions_csv)

CSV = """fileName,date,description,status,publicScore
submission.tar.gz,2026-07-12 04:00:00,lucario-heuristic v1.0 | deck=mega-lucario-fighting | agent=heuristic | abcd1234 | factory,COMPLETE,1543.2
submission.tar.gz,2026-07-11 04:00:00,older thing,PENDING,None
"""


def test_parse_submissions_csv_rows_and_scores():
    rows = parse_submissions_csv(CSV)
    assert rows[0].public_score == 1543.2
    assert rows[0].status == "COMPLETE"
    assert rows[0].description.startswith("lucario-heuristic v1.0")
    assert rows[1].public_score is None  # "None" -> None


def test_kaggle_client_builds_uvx_commands_and_raises_on_failure(tmp_path):
    calls = []

    class Proc:
        def __init__(self, code, out):
            self.returncode, self.stdout, self.stderr = code, out, "boom"

    def runner(cmd, **kw):
        calls.append(cmd)
        return Proc(0, CSV)

    client = KaggleClient(runner=runner)
    rows = client.list_submissions()
    assert rows[0].public_score == 1543.2
    assert calls[0][:4] == ["uvx", "kaggle", "competitions", "submissions"]
    client.submit(tmp_path / "submission.tar.gz", "desc here")
    assert calls[1][:4] == ["uvx", "kaggle", "competitions", "submit"]
    assert "-m" in calls[1] and "desc here" in calls[1]

    failing = KaggleClient(runner=lambda cmd, **kw: Proc(1, ""))
    with pytest.raises(RuntimeError, match="kaggle CLI failed"):
        failing.list_submissions()


def test_fake_client_records_submissions():
    fake = FakeKaggleClient()
    fake.submit(Path("x.tar.gz"), "name v1.0 | factory")
    assert fake.submitted == [(Path("x.tar.gz"), "name v1.0 | factory")]
    assert fake.list_submissions()[0].status == "PENDING"
    assert isinstance(fake.list_submissions()[0], SubmissionRow)
```

- [ ] Write the failing tests `tests/test_factory_harvest.py` (vector hand-verified: reprioritize bump 0.5 + 0.05 = 0.55):

```python
"""Tests for the ladder harvester + LADDER.md writer + reprioritization hook."""
from __future__ import annotations

from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.harvest import harvest, reprioritize
from ptcg.factory.kaggle_client import FakeKaggleClient, SubmissionRow


def _submitted(name: str, version: str, at: str, wr: float = 0.5) -> Candidate:
    c = Candidate.create(name=name, version=version, deck="d.csv",
                         agent_kind="heuristic")
    c.status = Status.SUBMITTED
    c.submitted_at = at
    c.local_wr = wr
    return c


def test_harvest_scores_candidates_and_writes_ladder(tmp_path):
    cand = _submitted("lucario-heuristic", "v1.0", "2026-07-12T04:00")
    rows = [SubmissionRow("submission.tar.gz", "2026-07-12 04:00:00",
                          "lucario-heuristic v1.0 | deck=x | factory",
                          "COMPLETE", 1543.2),
            SubmissionRow("submission.tar.gz", "2026-07-12 02:00:00",
                          "someone-elses manual upload", "COMPLETE", 1400.0)]
    ladder = tmp_path / "LADDER.md"
    res = harvest(FakeKaggleClient(rows), [cand], ladder, today="2026-07-12")
    assert cand.kaggle_score == 1543.2
    assert cand.status is Status.SCORED
    assert cand.score_history[-1][1] == 1543.2
    assert res.matched == 1 and res.scored_updates == 1
    assert res.submissions_today == 2  # BOTH rows count toward the 5/day cap
    assert res.unmatched_descriptions == ["someone-elses manual upload"]
    text = ladder.read_text(encoding="utf-8")
    assert "lucario-heuristic-v1.0" in text and "counted" in text


def test_harvest_is_idempotent_on_scores(tmp_path):
    cand = _submitted("lucario-heuristic", "v1.0", "2026-07-12T04:00")
    rows = [SubmissionRow("s.tar.gz", "2026-07-12 04:00:00",
                          "lucario-heuristic v1.0 | factory", "COMPLETE", 1543.2)]
    ladder = tmp_path / "LADDER.md"
    harvest(FakeKaggleClient(rows), [cand], ladder, today="2026-07-12")
    harvest(FakeKaggleClient(rows), [cand], ladder, today="2026-07-12")
    assert len(cand.score_history) == 1  # unchanged score appends nothing


def test_ladder_marks_only_two_most_recent_as_counted(tmp_path):
    old = _submitted("a-heuristic", "v1.0", "2026-07-01T00:00")
    mid = _submitted("b-heuristic", "v1.0", "2026-07-10T00:00")
    new = _submitted("c-heuristic", "v1.0", "2026-07-11T00:00")
    ladder = tmp_path / "LADDER.md"
    harvest(FakeKaggleClient([]), [old, mid, new], ladder, today="2026-07-12")
    lines = ladder.read_text(encoding="utf-8").splitlines()
    row = {ln.split("|")[1].strip(): ln for ln in lines if ln.startswith("| ")}
    assert "counted" in row["c-heuristic-v1.0"] and "counted" in row["b-heuristic-v1.0"]
    assert "superseded" in row["a-heuristic-v1.0"]


def test_reprioritize_bumps_queued_same_name():
    v1 = _submitted("x-net", "v1.0", "2026-07-01T00:00")
    v1.kaggle_score, v1.status = 1500.0, Status.SCORED
    v2 = _submitted("x-net", "v1.1", "2026-07-10T00:00")
    v2.kaggle_score, v2.status = 1550.0, Status.SCORED
    queued = Candidate.create(name="x-net", version="v1.2", deck="d.csv",
                              agent_kind="heuristic", priority=0.5)
    changed = reprioritize([v1, v2, queued])
    # Hand-verified 2026-07-11: improvement -> +0.05; 0.5 + 0.05 = 0.55
    assert changed == 1 and queued.priority == 0.55
```

- [ ] Run to see them fail: `uv run pytest tests/test_factory_kaggle_client.py tests/test_factory_harvest.py -q` — expected: `ModuleNotFoundError`.
- [ ] Create `src/ptcg/factory/kaggle_client.py`:

```python
"""Thin wrapper over the Kaggle CLI, run via uvx (spec S1/S5).

Auth is cached in ~/.kaggle (one-time `uvx kaggle auth login`). Column names in
parse_submissions_csv were verified against the real CLI output in the T7
investigation step (see the appendix in ANALYSIS-slice7a-asymmetric-test.md);
both camelCase and snake_case variants are tolerated.
"""
from __future__ import annotations

import csv
import io
import subprocess
from dataclasses import dataclass
from pathlib import Path

COMPETITION = "pokemon-tcg-ai-battle"


@dataclass
class SubmissionRow:
    file_name: str
    date: str
    description: str
    status: str
    public_score: float | None


def _parse_score(raw: str) -> float | None:
    raw = (raw or "").strip()
    if raw in ("", "None", "null", "-"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_submissions_csv(text: str) -> list[SubmissionRow]:
    reader = csv.DictReader(io.StringIO(text.strip()))
    rows: list[SubmissionRow] = []
    for r in reader:
        def get(*keys: str) -> str:
            for k in keys:
                if k in r and r[k] is not None:
                    return r[k]
            return ""
        rows.append(SubmissionRow(
            file_name=get("fileName", "file_name", "fileNameNullable"),
            date=get("date"),
            description=get("description", "descriptionNullable"),
            status=get("status"),
            public_score=_parse_score(get("publicScore", "public_score",
                                          "publicScoreNullable")),
        ))
    return rows


class KaggleClient:
    def __init__(self, competition: str = COMPETITION, runner=subprocess.run) -> None:
        self.competition = competition
        self._run = runner

    def _cli(self, *args: str) -> str:
        cmd = ["uvx", "kaggle", *args]
        proc = self._run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"kaggle CLI failed ({proc.returncode}): "
                               f"{proc.stderr.strip()[:500]}")
        return proc.stdout

    def list_submissions(self) -> list[SubmissionRow]:
        out = self._cli("competitions", "submissions", "-c", self.competition, "--csv")
        return parse_submissions_csv(out)

    def submit(self, bundle: Path, description: str) -> None:
        self._cli("competitions", "submit", "-c", self.competition,
                  "-f", str(bundle), "-m", description)


class FakeKaggleClient:
    """Duck-typed test double for KaggleClient (spec S9 dry-run mode)."""

    def __init__(self, rows: list[SubmissionRow] | None = None) -> None:
        self.rows = list(rows or [])
        self.submitted: list[tuple[Path, str]] = []

    def list_submissions(self) -> list[SubmissionRow]:
        return list(self.rows)

    def submit(self, bundle: Path, description: str) -> None:
        self.submitted.append((Path(bundle), description))
        self.rows.insert(0, SubmissionRow(Path(bundle).name, "2026-01-01 00:00:00",
                                          description, "PENDING", None))
```

- [ ] Create `src/ptcg/factory/harvest.py`:

```python
"""Event-driven ladder harvest (spec S5): pull freshest Kaggle data, map scores
onto candidates, regenerate experiments/LADDER.md, re-prioritize the queue."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path

from ptcg.factory.candidates import Candidate, Status, parse_version
from ptcg.factory.kaggle_client import SubmissionRow

LADDER_HEADER = (
    "# Ladder Log - Kaggle submissions (auto-written by the factory harvester)\n\n"
    "One row per factory submission: versioned identity, score trajectory, verdict.\n\n"
    "| Version | Submitted | Description | Status | Score trajectory | Verdict |\n"
    "|---------|-----------|-------------|--------|------------------|---------|\n"
)


@dataclass
class HarvestResult:
    matched: int = 0
    scored_updates: int = 0
    submissions_today: int = 0
    unmatched_descriptions: list[str] = field(default_factory=list)


def match_row(candidates: list[Candidate], row: SubmissionRow) -> Candidate | None:
    for cand in candidates:
        if row.description.startswith(f"{cand.name} {cand.version}"):
            return cand
    return None


def harvest(client, candidates: list[Candidate], ladder_path: Path,
            today: str | None = None) -> HarvestResult:
    today = today or dt.date.today().isoformat()
    rows = client.list_submissions()
    res = HarvestResult()
    for row in rows:
        if row.date.startswith(today):
            res.submissions_today += 1  # ALL uploads count toward the 5/day cap
        cand = match_row(candidates, row)
        if cand is None:
            res.unmatched_descriptions.append(row.description)
            continue
        res.matched += 1
        if row.public_score is not None:
            if cand.kaggle_score != row.public_score:
                cand.score_history.append(
                    [dt.datetime.now().isoformat(timespec="minutes"),
                     row.public_score])
                cand.kaggle_score = row.public_score
                res.scored_updates += 1
            if cand.status is Status.SUBMITTED:
                cand.status = Status.SCORED
    write_ladder(ladder_path, candidates, rows)
    return res


def write_ladder(path: Path, candidates: list[Candidate],
                 rows: list[SubmissionRow]) -> None:
    """Full idempotent regeneration (never append-drift), atomic write."""
    submitted = [c for c in candidates if c.submitted_at is not None]
    submitted.sort(key=lambda c: c.submitted_at, reverse=True)
    counted = {c.id for c in submitted[:2]}  # Kaggle: two most recent count
    lines = [LADDER_HEADER]
    for c in submitted:
        traj = " -> ".join(f"{score:.1f}" for _, score in c.score_history) or "pending"
        verdict = "counted" if c.id in counted else "superseded"
        desc = next((r.description for r in rows
                     if r.description.startswith(f"{c.name} {c.version}")), "")
        lines.append(f"| {c.id} | {c.submitted_at} | {desc} | {c.status.value} "
                     f"| {traj} | {verdict} |\n")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, path)


def reprioritize(candidates: list[Candidate]) -> int:
    """Score-trajectory hook (deliberately simple; spec S10 bans bandit math):
    latest scored version improved on its predecessor -> queued same-name
    candidates +0.05 priority; regressed -> -0.05. Clamped to [0, 1]."""
    changed = 0
    by_name: dict[str, list[Candidate]] = {}
    for c in candidates:
        if c.kaggle_score is not None:
            by_name.setdefault(c.name, []).append(c)
    for name, scored in by_name.items():
        if len(scored) < 2:
            continue
        scored.sort(key=lambda c: parse_version(c.version))
        delta = 0.05 if scored[-1].kaggle_score > scored[-2].kaggle_score else -0.05
        for c in candidates:
            if c.name == name and c.status is Status.QUEUED:
                c.priority = min(1.0, max(0.0, c.priority + delta))
                changed += 1
    return changed
```

- [ ] Run to pass: `uv run pytest tests/test_factory_kaggle_client.py tests/test_factory_harvest.py tests/test_experiments_append.py -q` — expected: all pass.
- [ ] REAL smoke of the read path (rung 3, read-only, no upload): `uv run python -c "import sys; sys.path.insert(0,'src'); from ptcg.factory.kaggle_client import KaggleClient; rows=KaggleClient().list_submissions(); print(len(rows), 'rows'); print(rows[0] if rows else 'no submissions yet')"` — expected: prints the real submission history (>= 1 row exists from prior slices) with a parsed score or None. If parsing drops fields, fix the header keys to the observed CSV before committing.
- [ ] Commit:

```
git add src/ptcg/factory/kaggle_client.py src/ptcg/factory/harvest.py tests/test_factory_kaggle_client.py tests/test_factory_harvest.py "experiments/ANALYSIS-slice7a-asymmetric-test.md"
git commit -m "feat: slice7a T7 - kaggle client + event-driven harvester + LADDER.md writer"
```

---

### Task 8: Submission gate + submitter

Spec S4: better-than-incumbent gate, exploration exception, 2/day cadence with merit override, hard 5/day cap via a restart-surviving persistent counter, description templating from versioned identity, one-retry upload policy, `--no-submit` dry-run path.

**Files:**

- Create: `src/ptcg/factory/gate.py`
- Create: `src/ptcg/factory/submit.py`
- Test: `tests/test_factory_gate.py`, `tests/test_factory_submit.py`

**Interfaces:**

- Consumes: `ptcg.factory.candidates`, `ptcg.factory.bundles` (`build_candidate_bundle`, `verify_candidate_bundle` — injectable for tests).
- Produces:
  - `HARD_DAILY_CAP = 5`
  - `@dataclass GateDecision(submit: bool, reason: str, exploratory: bool = False, mark_below_incumbent: bool = False)`
  - `class SubmissionCounter(path)` with `today_count(today) -> int`, `record(today)`, `reconcile(today, observed_today)` (floors at the harvested ladder count; never decreases); JSON state `{"date", "count"}`, atomic utf-8 write.
  - `incumbent(candidates) -> Candidate | None` — weaker `local_wr` of the two most recent submitted/scored.
  - `decide(candidate, candidates, counter, today, cadence_per_day=2, merit_margin=0.05, parity_band=0.02) -> GateDecision`.
  - `submission_description(candidate, commit, exploratory) -> str` — `"{name} {version} | deck={deck-stem} | agent={agent_kind} | local_wr={wr:.3f}/{games} | {commit} | factory[ [exploratory]]"` (harvest matches on the `"{name} {version}"` prefix).
  - `submit_candidates(candidates, client, counter, out_dir, repo, no_submit=False, today=None, cadence_per_day=2, build_fn=build_candidate_bundle, verify_fn=verify_candidate_bundle, log=print) -> list[tuple[str, str, str]]` (actions: `submitted` / `dry-run` / `skip` / `upload-failed`).

**Steps:**

- [ ] Write the failing test `tests/test_factory_gate.py` (margins hand-verified: 0.55-0.48=0.07>=0.05 merit override; 0.50-0.48=0.02<0.05 cadence defer; -0.01>=-0.02 parity band):

```python
"""Tests for the submission gate: incumbent, cadence, cap, exploration exception."""
from __future__ import annotations

from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.gate import HARD_DAILY_CAP, SubmissionCounter, decide, incumbent


def _sub(name: str, wr: float, at: str) -> Candidate:
    c = Candidate.create(name=name, version="v1.0", deck="d.csv",
                         agent_kind="heuristic")
    c.status, c.local_wr, c.submitted_at = Status.SUBMITTED, wr, at
    return c


def _evaluated(name: str, wr: float, novel: bool = False) -> Candidate:
    c = Candidate.create(name=name, version="v1.0", deck="d.csv",
                         agent_kind="heuristic", novel_axis=novel)
    c.status, c.local_wr, c.local_games = Status.EVALUATED, wr, 150
    return c


def test_counter_persists_across_restart_and_day_rollover(tmp_path):
    p = tmp_path / "counter.json"
    c1 = SubmissionCounter(p)
    assert c1.today_count("2026-07-12") == 0
    c1.record("2026-07-12")
    c1.record("2026-07-12")
    c2 = SubmissionCounter(p)  # simulated restart: state reloaded from disk
    assert c2.today_count("2026-07-12") == 2
    assert c2.today_count("2026-07-13") == 0  # day rollover resets


def test_counter_reconcile_floors_at_observed(tmp_path):
    c = SubmissionCounter(tmp_path / "counter.json")
    c.record("2026-07-12")
    c.reconcile("2026-07-12", observed_today=4)
    assert c.today_count("2026-07-12") == 4
    c.reconcile("2026-07-12", observed_today=1)  # never decreases
    assert c.today_count("2026-07-12") == 4


def test_incumbent_is_weaker_of_two_most_recent():
    old = _sub("old", 0.90, "2026-07-01T00:00")
    a = _sub("a", 0.55, "2026-07-10T00:00")
    b = _sub("b", 0.48, "2026-07-11T00:00")
    assert incumbent([old, a, b]).name == "b"  # 0.48 < 0.55; old's 0.90 not counted
    assert incumbent([]) is None


def test_gate_paths(tmp_path):
    counter = SubmissionCounter(tmp_path / "c.json")
    inc = _sub("inc", 0.48, "2026-07-11T00:00")

    better = _evaluated("better", 0.55)
    d = decide(better, [inc, better], counter, "2026-07-12")
    assert d.submit and "beats incumbent" in d.reason

    # hard cap blocks everything
    for _ in range(HARD_DAILY_CAP):
        counter.record("2026-07-12")
    d = decide(better, [inc, better], counter, "2026-07-12")
    assert not d.submit and "hard cap" in d.reason

    # cadence defers a small edge, merit overrides it (verified 2026-07-11:
    # 0.50-0.48=0.02 < 0.05 defers; 0.55-0.48=0.07 >= 0.05 overrides)
    counter2 = SubmissionCounter(tmp_path / "c2.json")
    counter2.record("2026-07-12")
    counter2.record("2026-07-12")  # cadence 2/day used up
    small = _evaluated("small", 0.50)
    assert not decide(small, [inc, small], counter2, "2026-07-12").submit
    assert decide(better, [inc, better], counter2, "2026-07-12").submit

    # exploration exception: novel axis at parity submits, flagged exploratory
    counter3 = SubmissionCounter(tmp_path / "c3.json")
    novel = _evaluated("novel", 0.47, novel=True)  # -0.01 within 0.02 parity band
    d = decide(novel, [inc, novel], counter3, "2026-07-12")
    assert d.submit and d.exploratory

    # same WR without the novel axis -> below incumbent
    plain = _evaluated("plain", 0.47)
    d = decide(plain, [inc, plain], counter3, "2026-07-12")
    assert not d.submit and d.mark_below_incumbent


def test_bootstrap_submits_when_no_incumbent(tmp_path):
    counter = SubmissionCounter(tmp_path / "c.json")
    cand = _evaluated("first", 0.52)
    d = decide(cand, [cand], counter, "2026-07-12")
    assert d.submit and "bootstrap" in d.reason
```

- [ ] Write the failing test `tests/test_factory_submit.py`:

```python
"""Tests for the submitter: descriptions, retry policy, dry-run, status flips."""
from __future__ import annotations

from pathlib import Path

from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.gate import SubmissionCounter
from ptcg.factory.kaggle_client import FakeKaggleClient
from ptcg.factory.submit import submission_description, submit_candidates


def _evaluated(name: str, wr: float) -> Candidate:
    c = Candidate.create(name=name, version="v1.0", deck="decks/mega-x.csv",
                         agent_kind="heuristic")
    c.status, c.local_wr, c.local_games = Status.EVALUATED, wr, 150
    return c


def _sub(name: str, wr: float, at: str) -> Candidate:
    c = _evaluated(name, wr)
    c.status, c.submitted_at = Status.SUBMITTED, at
    return c


def test_description_carries_versioned_identity():
    cand = _evaluated("mega-x-heuristic", 0.55)
    desc = submission_description(cand, "abcd1234", exploratory=True)
    assert desc.startswith("mega-x-heuristic v1.0 | deck=mega-x | agent=heuristic")
    assert "local_wr=0.550/150" in desc and "abcd1234" in desc
    assert desc.endswith("factory [exploratory]")


def _no_bundle(cand, out_dir):
    return Path(out_dir) / "submission.tar.gz"


def test_submit_flow_dry_run_and_real(tmp_path):
    inc = _sub("inc", 0.48, "2026-07-11T00:00")
    cand = _evaluated("better", 0.55)
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    actions = submit_candidates([inc, cand], client, counter, tmp_path, tmp_path,
                                no_submit=True, today="2026-07-12",
                                build_fn=_no_bundle, verify_fn=lambda *a: None,
                                log=lambda m: None)
    assert actions[0][1] == "dry-run"
    assert client.submitted == [] and cand.status is Status.EVALUATED

    actions = submit_candidates([inc, cand], client, counter, tmp_path, tmp_path,
                                today="2026-07-12", build_fn=_no_bundle,
                                verify_fn=lambda *a: None, log=lambda m: None)
    assert actions[0][1] == "submitted"
    assert cand.status is Status.SUBMITTED and cand.submitted_at is not None
    assert counter.today_count("2026-07-12") == 1
    assert client.submitted[0][1].startswith("better v1.0")


def test_upload_retries_once_then_defers(tmp_path):
    inc = _sub("inc", 0.48, "2026-07-11T00:00")
    cand = _evaluated("better", 0.55)
    counter = SubmissionCounter(tmp_path / "c.json")

    class FlakyClient(FakeKaggleClient):
        def __init__(self, fail_times: int):
            super().__init__()
            self.fail_times = fail_times

        def submit(self, bundle, description):
            if self.fail_times > 0:
                self.fail_times -= 1
                raise RuntimeError("network")
            super().submit(bundle, description)

    ok_after_one = FlakyClient(1)
    actions = submit_candidates([inc, cand], ok_after_one, counter, tmp_path,
                                tmp_path, today="2026-07-12", build_fn=_no_bundle,
                                verify_fn=lambda *a: None, log=lambda m: None)
    assert actions[0][1] == "submitted"  # first retry succeeded

    cand2 = _evaluated("better2", 0.60)
    always_fails = FlakyClient(99)
    actions = submit_candidates([inc, cand2], always_fails, counter, tmp_path,
                                tmp_path, today="2026-07-12", build_fn=_no_bundle,
                                verify_fn=lambda *a: None, log=lambda m: None)
    assert actions[0][1] == "upload-failed"
    assert cand2.status is Status.EVALUATED  # deferred to next cycle, not lost
    assert "upload-failed" in cand2.notes
```

- [ ] Run to see them fail: `uv run pytest tests/test_factory_gate.py tests/test_factory_submit.py -q` — expected: `ModuleNotFoundError`.
- [ ] Create `src/ptcg/factory/gate.py`:

```python
"""Submission gate (spec S4): better-than-incumbent, exploration exception,
cadence rhythm with merit override, persistent hard 5/day cap."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ptcg.factory.candidates import Candidate, Status

HARD_DAILY_CAP = 5  # Kaggle rule; never configurable upward


@dataclass
class GateDecision:
    submit: bool
    reason: str
    exploratory: bool = False
    mark_below_incumbent: bool = False


class SubmissionCounter:
    """Persistent daily submission counter; survives restarts (spec S9)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if self.path.exists():
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            self.date, self.count = doc.get("date"), int(doc.get("count", 0))
        else:
            self.date, self.count = None, 0

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"date": self.date, "count": self.count}),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def today_count(self, today: str) -> int:
        return self.count if self.date == today else 0

    def record(self, today: str) -> None:
        self.count = self.today_count(today) + 1
        self.date = today
        self._save()

    def reconcile(self, today: str, observed_today: int) -> None:
        """Floor at what the harvested ladder shows (manual uploads count too);
        never decreases within a day."""
        self.count = max(self.today_count(today), observed_today)
        self.date = today
        self._save()


def incumbent(candidates: list[Candidate]) -> Candidate | None:
    """Weaker (by local WR) of the two most recently submitted/scored candidates."""
    submitted = [c for c in candidates
                 if c.status in (Status.SUBMITTED, Status.SCORED) and c.submitted_at]
    submitted.sort(key=lambda c: c.submitted_at, reverse=True)
    counted = submitted[:2]
    if not counted:
        return None
    with_wr = [c for c in counted if c.local_wr is not None]
    return min(with_wr, key=lambda c: c.local_wr) if with_wr else counted[-1]


def decide(candidate: Candidate, candidates: list[Candidate],
           counter: SubmissionCounter, today: str, cadence_per_day: int = 2,
           merit_margin: float = 0.05, parity_band: float = 0.02) -> GateDecision:
    if candidate.status is not Status.EVALUATED:
        return GateDecision(False, f"status={candidate.status.value}, not evaluated")
    used = counter.today_count(today)
    if used >= HARD_DAILY_CAP:
        return GateDecision(False, f"hard cap reached ({used}/{HARD_DAILY_CAP})")
    inc = incumbent(candidates)
    if inc is None or inc.local_wr is None:
        return GateDecision(True, "no incumbent with local WR - bootstrap submit")
    edge = (candidate.local_wr or 0.0) - inc.local_wr
    if edge > 0:
        if used >= cadence_per_day and edge < merit_margin:
            return GateDecision(False, f"cadence {cadence_per_day}/day used and edge "
                                       f"{edge:+.3f} < merit margin {merit_margin}")
        return GateDecision(True, f"beats incumbent {inc.id} by {edge:+.3f}")
    if candidate.novel_axis and edge >= -parity_band:
        if used >= cadence_per_day:
            return GateDecision(False, "novel axis at parity but cadence used")
        return GateDecision(True, f"exploration exception: novel axis at parity "
                                  f"({edge:+.3f})", exploratory=True)
    return GateDecision(False, f"below incumbent {inc.id} by {edge:+.3f}",
                        mark_below_incumbent=True)
```

- [ ] Create `src/ptcg/factory/submit.py`:

```python
"""Build, verify, and upload gated candidates (spec S4/S9). Upload failures get
one retry, then defer to the next cycle without blocking evaluation."""
from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path
from typing import Callable

from ptcg.factory import gate as gate_mod
from ptcg.factory.bundles import build_candidate_bundle, verify_candidate_bundle
from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.gate import SubmissionCounter


def git_head(repo: Path) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], cwd=repo,
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def submission_description(candidate: Candidate, commit: str,
                           exploratory: bool) -> str:
    tag = " [exploratory]" if exploratory else ""
    return (f"{candidate.name} {candidate.version} "
            f"| deck={Path(candidate.deck).stem} "
            f"| agent={candidate.agent_kind} "
            f"| local_wr={candidate.local_wr:.3f}/{candidate.local_games} "
            f"| {commit} | factory{tag}")


def submit_candidates(candidates: list[Candidate], client,
                      counter: SubmissionCounter, out_dir: Path, repo: Path,
                      no_submit: bool = False, today: str | None = None,
                      cadence_per_day: int = 2,
                      build_fn: Callable = build_candidate_bundle,
                      verify_fn: Callable = verify_candidate_bundle,
                      log: Callable = print) -> list[tuple[str, str, str]]:
    today = today or dt.date.today().isoformat()
    commit = git_head(Path(repo))
    actions: list[tuple[str, str, str]] = []
    ranked = sorted((c for c in candidates if c.status is Status.EVALUATED),
                    key=lambda c: -(c.local_wr or 0.0))
    for cand in ranked:
        decision = gate_mod.decide(cand, candidates, counter, today,
                                   cadence_per_day=cadence_per_day)
        if not decision.submit:
            if decision.mark_below_incumbent:
                cand.status = Status.BELOW_INCUMBENT
            log(f"skip {cand.id}: {decision.reason}")
            actions.append((cand.id, "skip", decision.reason))
            continue
        bundle = build_fn(cand, out_dir)
        verify_fn(cand, bundle, Path(out_dir) / "submission")
        description = submission_description(cand, commit, decision.exploratory)
        if no_submit:
            log(f"DRY-RUN would submit {cand.id}: {description}")
            actions.append((cand.id, "dry-run", description))
            continue
        try:
            client.submit(bundle, description)
        except Exception as exc:
            log(f"upload failed for {cand.id}, retrying once: {exc!r}")
            try:
                client.submit(bundle, description)
            except Exception as exc2:
                cand.notes = f"upload-failed: {exc2!r}"[:300]
                log(f"upload failed twice for {cand.id}; deferring to next cycle")
                actions.append((cand.id, "upload-failed", repr(exc2)))
                continue
        counter.record(today)
        cand.status = Status.SUBMITTED
        cand.submitted_at = dt.datetime.now().isoformat(timespec="minutes")
        log(f"submitted {cand.id}: {description}")
        actions.append((cand.id, "submitted", description))
    return actions
```

- [ ] Run to pass: `uv run pytest tests/test_factory_gate.py tests/test_factory_submit.py tests/test_experiments_append.py -q` — expected: all pass.
- [ ] Commit:

```
git add src/ptcg/factory/gate.py src/ptcg/factory/submit.py tests/test_factory_gate.py tests/test_factory_submit.py
git commit -m "feat: slice7a T8 - incumbent gate, exploration exception, persistent 5/day cap, submitter"
```

---

### Task 9: Factory cycle (harvest-first wiring + digest + journal)

Spec S1/S6: one cycle = harvest -> reconcile counter -> re-prioritize -> evaluate -> gate/submit -> digest + methodology journal. End-to-end dry-run test with FakeKaggleClient + stubbed arena + stubbed bundle build (the REAL bundle/smoke path is exercised in T13).

**Files:**

- Create: `src/ptcg/factory/journal.py`
- Create: `src/ptcg/factory/cycle.py`
- Create: `scripts/factory_cycle.py`
- Test: `tests/test_factory_cycle.py`

**Interfaces:**

- Consumes: everything from T3-T8.
- Produces:
  - `append_journal(path: Path, title: str, body: str, now=None) -> None` — append-only `docs/writeup-notes.md` writer (creates header if missing; open mode `"a"`, `encoding="utf-8"`).
  - `@dataclass FactoryPaths(root: Path)` with properties `ledger` (`experiments/factory/candidates.json`), `counter` (`experiments/factory/submission_counter.json`), `ladder` (`experiments/LADDER.md`), `digest_dir` (`experiments/factory/digests`), `journal` (`docs/writeup-notes.md`), `bundle_dir` (`build/factory`), `pause_file` (`experiments/factory/PAUSE`), `log_dir` (`experiments/factory/logs`), `experiments_md` (`experiments/EXPERIMENTS.md`).
  - `write_digest(digest_dir, harvest_res, evaluated, actions, now=None) -> Path` — one human-readable md file per cycle.
  - `run_cycle(paths, client, eval_cfg, *, no_submit=False, cadence_per_day=2, series_fn=None, build_fn=None, verify_fn=None, now=None, log=print) -> dict` — returns `{"paused"| "harvest", "evaluated", "actions", "digest"}`.
  - CLI `scripts/factory_cycle.py [--no-submit] [--games N] [--budget-minutes F] [--max-candidates N] [--cadence N]`, tee-logging to `experiments/factory/logs/cycle-<ts>.log`.

**Steps:**

- [ ] Write the failing test `tests/test_factory_cycle.py`:

```python
"""End-to-end dry-run of one factory cycle (fake Kaggle, stubbed arena/bundles)."""
from __future__ import annotations

from pathlib import Path

from ptcg.arena.stats import SeriesStats
from ptcg.factory.candidates import Candidate, Status, load_ledger, save_ledger
from ptcg.factory.cycle import FactoryPaths, run_cycle
from ptcg.factory.evaluate import EvalConfig
from ptcg.factory.journal import append_journal
from ptcg.factory.kaggle_client import FakeKaggleClient, SubmissionRow


def _stats(wins_a: int, wins_b: int) -> SeriesStats:
    return SeriesStats(wins_a=wins_a, wins_b=wins_b,
                       game_seconds=[0.1] * (wins_a + wins_b))


def test_journal_appends_and_creates_header(tmp_path):
    j = tmp_path / "writeup-notes.md"
    append_journal(j, "first entry", "body one")
    append_journal(j, "second entry", "body two")
    text = j.read_text(encoding="utf-8")
    assert text.startswith("# Writeup Notes")
    assert text.count("## ") >= 2 and "body two" in text


def test_cycle_dry_run_end_to_end(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    inc = Candidate.create(name="incumbent", version="v1.0", deck="d.csv",
                           agent_kind="heuristic")
    inc.status, inc.local_wr, inc.local_games = Status.SUBMITTED, 0.50, 150
    inc.submitted_at = "2026-07-11T04:00"
    challenger = Candidate.create(
        name="challenger", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", priority=0.9)
    save_ledger(paths.ledger, [inc, challenger])

    client = FakeKaggleClient(rows=[SubmissionRow(
        "submission.tar.gz", "2026-07-11 04:00:00",
        "incumbent v1.0 | deck=d | agent=heuristic | x | factory",
        "COMPLETE", 1500.0)])

    result = run_cycle(
        paths, client, EvalConfig(games=150, experiments_md=paths.experiments_md),
        no_submit=True, series_fn=lambda c, cfg: _stats(90, 60),
        build_fn=lambda c, d: Path(d) / "submission.tar.gz",
        verify_fn=lambda *a: None, log=lambda m: None)

    cands = {c.name: c for c in load_ledger(paths.ledger)}
    assert cands["incumbent"].status is Status.SCORED  # harvest matched + scored
    assert cands["challenger"].status is Status.EVALUATED
    assert cands["challenger"].local_wr == 0.6
    assert any(a[1] == "dry-run" for a in result["actions"])  # 0.6 > 0.5 gate pass
    assert client.submitted == []  # dry-run uploads nothing
    assert paths.ladder.exists()
    assert "slice7a-factory-eval challenger-v1.0" in paths.experiments_md.read_text(
        encoding="utf-8")
    digest = Path(result["digest"]).read_text(encoding="utf-8")
    assert "challenger-v1.0" in digest
    assert "factory cycle" in paths.journal.read_text(encoding="utf-8")


def test_cycle_respects_pause_file(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    paths.pause_file.parent.mkdir(parents=True, exist_ok=True)
    paths.pause_file.write_text("paused for review", encoding="utf-8")
    result = run_cycle(paths, FakeKaggleClient(), EvalConfig(), log=lambda m: None)
    assert result == {"paused": True}


def test_cycle_survives_harvest_outage(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    cand = Candidate.create(
        name="only", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", priority=0.9)
    save_ledger(paths.ledger, [cand])

    class DeadClient:
        def list_submissions(self):
            raise RuntimeError("kaggle CLI failed (1): offline")

        def submit(self, bundle, description):
            raise AssertionError("must not upload during outage test")

    result = run_cycle(
        paths, DeadClient(), EvalConfig(experiments_md=paths.experiments_md),
        no_submit=True, series_fn=lambda c, cfg: _stats(90, 60),
        build_fn=lambda c, d: Path(d) / "s.tar.gz", verify_fn=lambda *a: None,
        log=lambda m: None)
    # harvest failure never blocks evaluation (spec S9)
    assert result["harvest"] is None
    assert load_ledger(paths.ledger)[0].status is Status.EVALUATED
```

- [ ] Run to see it fail: `uv run pytest tests/test_factory_cycle.py -q` — expected: `ModuleNotFoundError`.
- [ ] Create `src/ptcg/factory/journal.py`:

```python
"""Append-only methodology journal writer (spec S6: docs/writeup-notes.md)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

JOURNAL_HEADER = (
    "# Writeup Notes - methodology journal (append-only)\n\n"
    "Factory scripts log decisions with reasons; the weekly review adds strategic\n"
    "prose. The Strategy report is an editing job over this file plus\n"
    "experiments/LADDER.md and experiments/EXPERIMENTS.md.\n"
)


def append_journal(path: Path, title: str, body: str,
                   now: dt.datetime | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now()).isoformat(timespec="minutes")
    if not path.exists():
        path.write_text(JOURNAL_HEADER, encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n## {stamp} - {title}\n\n{body}\n")
```

- [ ] Create `src/ptcg/factory/cycle.py`:

```python
"""One factory cycle (spec S1): harvest FIRST (event-driven freshness), then
re-prioritize, evaluate, gate/submit, and write the digest + journal entry."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ptcg.factory import harvest as harvest_mod
from ptcg.factory.candidates import load_ledger, save_ledger
from ptcg.factory.evaluate import EvalConfig, append_experiments_row, evaluate_queued
from ptcg.factory.gate import SubmissionCounter
from ptcg.factory.journal import append_journal
from ptcg.factory.submit import submit_candidates


@dataclass
class FactoryPaths:
    root: Path

    @property
    def ledger(self) -> Path:
        return self.root / "experiments" / "factory" / "candidates.json"

    @property
    def counter(self) -> Path:
        return self.root / "experiments" / "factory" / "submission_counter.json"

    @property
    def ladder(self) -> Path:
        return self.root / "experiments" / "LADDER.md"

    @property
    def digest_dir(self) -> Path:
        return self.root / "experiments" / "factory" / "digests"

    @property
    def journal(self) -> Path:
        return self.root / "docs" / "writeup-notes.md"

    @property
    def bundle_dir(self) -> Path:
        return self.root / "build" / "factory"

    @property
    def pause_file(self) -> Path:
        return self.root / "experiments" / "factory" / "PAUSE"

    @property
    def log_dir(self) -> Path:
        return self.root / "experiments" / "factory" / "logs"

    @property
    def experiments_md(self) -> Path:
        return self.root / "experiments" / "EXPERIMENTS.md"


def write_digest(digest_dir: Path, harvest_res, evaluated, actions,
                 now: dt.datetime | None = None) -> Path:
    now = now or dt.datetime.now()
    digest_dir = Path(digest_dir)
    digest_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# Factory cycle digest - {now.isoformat(timespec='minutes')}\n"]
    if harvest_res is None:
        lines.append("\nHarvest: FAILED/offline (evaluation proceeded).\n")
    else:
        lines.append(f"\nHarvest: {harvest_res.matched} matched rows, "
                     f"{harvest_res.scored_updates} score updates, "
                     f"{harvest_res.submissions_today} submissions today.\n")
        for d in harvest_res.unmatched_descriptions:
            lines.append(f"- unmatched submission: {d}\n")
    lines.append("\n## Evaluated\n")
    if evaluated:
        for c in evaluated:
            lines.append(f"- {c.id}: local_wr={c.local_wr:.3f}/{c.local_games} "
                         f"-> {c.status.value}\n")
    else:
        lines.append("- none (empty queue or budget exhausted)\n")
    lines.append("\n## Gate actions\n")
    if actions:
        for cid, action, detail in actions:
            lines.append(f"- {cid}: {action} - {detail}\n")
    else:
        lines.append("- none\n")
    path = digest_dir / f"cycle-{now:%Y%m%d-%H%M%S}.md"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def run_cycle(paths: FactoryPaths, client, eval_cfg: EvalConfig, *,
              no_submit: bool = False, cadence_per_day: int = 2,
              series_fn: Callable | None = None, build_fn: Callable | None = None,
              verify_fn: Callable | None = None,
              now: dt.datetime | None = None, log: Callable = print) -> dict:
    if paths.pause_file.exists():
        log("factory paused (PAUSE file present) - skipping cycle")
        return {"paused": True}
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    candidates = load_ledger(paths.ledger)
    counter = SubmissionCounter(paths.counter)

    harvest_res = None
    try:  # 1. harvest first; an outage never blocks evaluation (spec S9)
        harvest_res = harvest_mod.harvest(client, candidates, paths.ladder,
                                          today=today)
        counter.reconcile(today, harvest_res.submissions_today)
        harvest_mod.reprioritize(candidates)
    except Exception as exc:
        log(f"harvest failed (continuing offline): {exc!r}")
    save_ledger(paths.ledger, candidates)

    # 2. evaluate top-priority queued candidates
    eval_kwargs: dict = {
        "append_row": lambda cand, stats: append_experiments_row(
            cand, stats, eval_cfg.experiments_md),
    }
    if series_fn is not None:
        eval_kwargs["series_fn"] = series_fn
    evaluated = evaluate_queued(candidates, eval_cfg, paths.ledger, save_ledger,
                                **eval_kwargs)

    # 3. gate + submit (or dry-run)
    submit_kwargs: dict = {}
    if build_fn is not None:
        submit_kwargs["build_fn"] = build_fn
    if verify_fn is not None:
        submit_kwargs["verify_fn"] = verify_fn
    actions = submit_candidates(candidates, client, counter, paths.bundle_dir,
                                paths.root, no_submit=no_submit, today=today,
                                cadence_per_day=cadence_per_day, log=log,
                                **submit_kwargs)
    save_ledger(paths.ledger, candidates)

    # 4. digest + methodology journal (spec S6)
    digest_path = write_digest(paths.digest_dir, harvest_res, evaluated, actions,
                               now=now)
    summary = (f"evaluated {len(evaluated)} candidate(s); "
               f"actions: {[f'{cid}:{act}' for cid, act, _ in actions] or 'none'}; "
               f"digest: {digest_path.name}")
    append_journal(paths.journal, f"factory cycle {today}", summary, now=now)
    return {"harvest": harvest_res, "evaluated": [c.id for c in evaluated],
            "actions": actions, "digest": str(digest_path)}
```

- [ ] Create `scripts/factory_cycle.py`:

```python
"""CLI: run one factory cycle (harvest -> evaluate -> gate -> submit -> digest).

Nightly entry point for Windows Task Scheduler; also run on demand. Tee-logs to
experiments/factory/logs/ so scheduled runs are debuggable after the fact."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory.cycle import FactoryPaths, run_cycle  # noqa: E402
from ptcg.factory.evaluate import EvalConfig  # noqa: E402
from ptcg.factory.kaggle_client import KaggleClient  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--no-submit", action="store_true",
                   help="dry-run: build+verify+gate but never upload")
    p.add_argument("--games", type=int, default=150)
    p.add_argument("--budget-minutes", type=float, default=240.0)
    p.add_argument("--max-candidates", type=int, default=None)
    p.add_argument("--cadence", type=int, default=2)
    args = p.parse_args()

    paths = FactoryPaths(root=ROOT)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = paths.log_dir / f"cycle-{dt.datetime.now():%Y%m%d-%H%M%S}.log"

    with log_path.open("a", encoding="utf-8") as log_file:
        def log(msg: str) -> None:
            print(msg, flush=True)
            log_file.write(msg + "\n")
            log_file.flush()

        cfg = EvalConfig(games=args.games, budget_minutes=args.budget_minutes,
                         max_candidates=args.max_candidates)
        result = run_cycle(paths, KaggleClient(), cfg, no_submit=args.no_submit,
                           cadence_per_day=args.cadence, log=log)
        log(f"cycle result: { {k: v for k, v in result.items() if k != 'harvest'} }")


if __name__ == "__main__":
    main()
```

- [ ] Run to pass: `uv run pytest tests/test_factory_cycle.py tests/test_experiments_append.py -q` — expected: all pass (guard now also covers `journal.py`/`cycle.py`/`scripts/factory_cycle.py`).
- [ ] Full suite: `uv run pytest -q` — expected: green.
- [ ] Commit:

```
git add src/ptcg/factory/journal.py src/ptcg/factory/cycle.py scripts/factory_cycle.py tests/test_factory_cycle.py
git commit -m "feat: slice7a T9 - factory cycle wiring, digest writer, methodology journal"
```

---

### Task 10: Scheduling + ops (combined-review eligible)

**Files:**

- Create: `scripts/register_factory_task.ps1`
- Create: `docs/factory-operations.md`

**Interfaces:**

- Consumes: `scripts/factory_cycle.py`, the `PAUSE`-file check already implemented in T9.
- Produces: a Windows Scheduled Task `ptcg-factory-nightly` (default 02:00 daily) running the cycle via `uv`; an operations doc.

**Steps:**

- [ ] Create `scripts/register_factory_task.ps1`:

```powershell
# Register (or remove) the nightly factory cycle as a Windows Scheduled Task.
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1 -At "03:30"
#   powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1 -Unregister
param(
    [string]$At = "02:00",
    [switch]$Unregister
)
$TaskName = "ptcg-factory-nightly"
$Repo = Split-Path -Parent $PSScriptRoot

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "unregistered $TaskName"
    exit 0
}

$uv = (Get-Command uv.exe).Source
$action = New-ScheduledTaskAction -Execute $uv `
    -Argument "run python scripts/factory_cycle.py" `
    -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "PTCG agent factory nightly cycle" -Force
Write-Output "registered $TaskName at $At daily (repo: $Repo)"
Write-Output "logs: experiments\factory\logs\  digests: experiments\factory\digests\"
```

- [ ] Create `docs/factory-operations.md` (ASCII-clean):

```markdown
# Operating the Agent Factory (Slice 7A)

## Commands

- One cycle now:            uv run python scripts/factory_cycle.py
- Dry-run (never uploads):  uv run python scripts/factory_cycle.py --no-submit
- Small/fast cycle:         uv run python scripts/factory_cycle.py --games 50 --max-candidates 2
- Seed/refresh candidates:  uv run python scripts/seed_candidates.py
- Training daemon (T12):    uv run python scripts/factory_daemon.py --deck src/ptcg/decks/candidates/<deck>.csv --cycles 2
- Register nightly task:    powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1
- Remove nightly task:      powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1 -Unregister

## Where things live

- Candidate queue ledger:   experiments/factory/candidates.json (git-tracked)
- Daily submission counter: experiments/factory/submission_counter.json (hard cap 5/day)
- Cycle digests:            experiments/factory/digests/cycle-<timestamp>.md (read these)
- Cycle logs:               experiments/factory/logs/cycle-<timestamp>.log
- Ladder log:               experiments/LADDER.md (auto-regenerated every harvest)
- Methodology journal:      docs/writeup-notes.md (append-only)
- Bundles are staged under: build/factory/ (not git-tracked)

## Pausing

Create the pause file and the next cycle exits immediately without harvesting,
evaluating, or submitting:

    echo paused > experiments/factory/PAUSE

Delete the file to resume:

    del experiments\factory\PAUSE     (PowerShell: Remove-Item experiments/factory/PAUSE)

## Submission policy knobs (scripts/factory_cycle.py flags)

- --cadence N        default 2/day rhythm; merit override (edge >= 0.05) bypasses it
- --no-submit        full pipeline, no upload
- Hard cap 5/day is NOT configurable (Kaggle rule); the counter survives restarts
  and is reconciled against harvested ladder rows every cycle (manual uploads count).

## Weekly review

Follow docs/weekly-review-checklist.md (created in T14): re-prioritize the queue
from LADDER.md trajectories, author new deck/config candidates, record strategy
decisions in docs/writeup-notes.md.
```

- [ ] Verify the registration script parses: `powershell -ExecutionPolicy Bypass -Command "Get-Command -Syntax scripts/register_factory_task.ps1"` (or register + immediately `-Unregister` if Brad approves a live check). Registration itself is deferred to T13's wrap-up so the nightly task never runs against a half-built factory.
- [ ] Commit:

```
git add scripts/register_factory_task.ps1 docs/factory-operations.md
git commit -m "feat: slice7a T10 - nightly Task Scheduler registration + factory operations doc"
```

---

### Task 11: Training daemon core (producer-consumer overlap)

Spec S8: CPU generates data for cycle N+1 while the GPU trains cycle N. Subprocess model for real trainers (the existing scripts are CLIs; two `ThreadPoolExecutor` workers each blocking on their own `subprocess.run` gives true CPU/GPU overlap with no GIL concern). Crash isolation + persisted state for resume; completed training runs auto-register a new candidate version.

**Files:**

- Create: `src/ptcg/factory/daemon.py`
- Test: `tests/test_factory_daemon.py`

**Interfaces:**

- Consumes: `ptcg.factory.candidates` (`Candidate`, `load_ledger`, `save_ledger`).
- Produces:
  - `class Trainer(Protocol)`: `name: str`; `prepare_data(cycle: int) -> Path` (CPU producer); `train(data_path: Path, cycle: int) -> Path` (GPU consumer, returns weights path); `export_and_register(weights_path: Path, cycle: int, candidates: list[Candidate]) -> Candidate`.
  - `@dataclass DaemonState(next_cycle: int = 0, completed: list = [], last_error: str | None = None)` + `load_state(path) -> DaemonState` / `save_state(path, state)` (atomic utf-8 JSON).
  - `run_daemon(trainer, n_cycles, ledger_path, state_path, log=print) -> DaemonState` — pipelined loop; on exception persists state (`next_cycle` unchanged -> rerun resumes at the failed cycle) and re-raises.

**Steps:**

- [ ] Write the failing test `tests/test_factory_daemon.py`:

```python
"""Tests for the continuous-training daemon (FakeTrainer - no real training)."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from ptcg.factory.candidates import Candidate, load_ledger, next_version
from ptcg.factory.daemon import DaemonState, load_state, run_daemon, save_state


class OverlapProbeTrainer:
    """train(N) blocks until prepare_data(N+1) has STARTED - if the daemon does
    not overlap producer and consumer, the wait times out and the test fails."""

    name = "probe"

    def __init__(self, n_cycles: int) -> None:
        self.prepare_started = {i: threading.Event() for i in range(n_cycles)}
        self.events: list[tuple[str, int]] = []

    def prepare_data(self, cycle: int) -> Path:
        self.prepare_started[cycle].set()
        self.events.append(("prepare", cycle))
        return Path(f"data-{cycle}.jsonl")

    def train(self, data_path: Path, cycle: int) -> Path:
        nxt = self.prepare_started.get(cycle + 1)
        if nxt is not None:
            assert nxt.wait(timeout=10), \
                f"prepare_data({cycle + 1}) never started while train({cycle}) ran"
        self.events.append(("train", cycle))
        return Path(f"weights-{cycle}.json")

    def export_and_register(self, weights_path: Path, cycle: int,
                            candidates: list[Candidate]) -> Candidate:
        return Candidate.create(
            name="probe-net", version=next_version(candidates, "probe-net"),
            deck="d.csv", agent_kind="search-net", provenance=f"daemon:probe:c{cycle}")


def test_daemon_overlaps_and_registers_candidates(tmp_path):
    trainer = OverlapProbeTrainer(n_cycles=2)
    ledger, state_p = tmp_path / "candidates.json", tmp_path / "daemon_state.json"
    state = run_daemon(trainer, 2, ledger, state_p, log=lambda m: None)
    assert state.next_cycle == 2 and state.completed == [0, 1]
    cands = load_ledger(ledger)
    assert [c.id for c in cands] == ["probe-net-v0.1", "probe-net-v0.2"]
    assert ("train", 0) in trainer.events and ("prepare", 1) in trainer.events


class FlakyTrainer(OverlapProbeTrainer):
    def __init__(self, n_cycles: int) -> None:
        super().__init__(n_cycles)
        self.fail_next_train = True

    def train(self, data_path: Path, cycle: int) -> Path:
        if self.fail_next_train:
            self.fail_next_train = False
            raise RuntimeError("gpu fell over")
        return super().train(data_path, cycle)


def test_daemon_persists_state_and_resumes_after_crash(tmp_path):
    ledger, state_p = tmp_path / "candidates.json", tmp_path / "daemon_state.json"
    flaky = FlakyTrainer(n_cycles=1)
    with pytest.raises(RuntimeError, match="gpu fell over"):
        run_daemon(flaky, 1, ledger, state_p, log=lambda m: None)
    state = load_state(state_p)
    assert state.next_cycle == 0 and state.last_error is not None  # resume point

    ok = OverlapProbeTrainer(n_cycles=1)
    state = run_daemon(ok, 1, ledger, state_p, log=lambda m: None)
    assert state.next_cycle == 1 and state.completed == [0]
    assert load_ledger(ledger)[0].id == "probe-net-v0.1"


def test_state_round_trip(tmp_path):
    p = tmp_path / "s.json"
    save_state(p, DaemonState(next_cycle=3, completed=[0, 1, 2]))
    s = load_state(p)
    assert s.next_cycle == 3 and s.completed == [0, 1, 2]
    assert load_state(tmp_path / "missing.json").next_cycle == 0
```

- [ ] Run to see it fail: `uv run pytest tests/test_factory_daemon.py -q` — expected: `ModuleNotFoundError`.
- [ ] Create `src/ptcg/factory/daemon.py`:

```python
"""Continuous-training daemon (spec S8): producer-consumer overlap behind a
pluggable Trainer interface. 7A occupant: PerDeckNetTrainer; 7B swaps in the
policy-improvement trainer behind the same Protocol with no factory rework."""
from __future__ import annotations

import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ptcg.factory.candidates import Candidate, load_ledger, save_ledger


class Trainer(Protocol):
    name: str

    def prepare_data(self, cycle: int) -> Path: ...      # CPU producer

    def train(self, data_path: Path, cycle: int) -> Path: ...  # GPU consumer

    def export_and_register(self, weights_path: Path, cycle: int,
                            candidates: list[Candidate]) -> Candidate: ...


@dataclass
class DaemonState:
    next_cycle: int = 0
    completed: list = field(default_factory=list)
    last_error: str | None = None


def load_state(path: Path) -> DaemonState:
    path = Path(path)
    if not path.exists():
        return DaemonState()
    doc = json.loads(path.read_text(encoding="utf-8"))
    return DaemonState(next_cycle=int(doc.get("next_cycle", 0)),
                       completed=list(doc.get("completed", [])),
                       last_error=doc.get("last_error"))


def save_state(path: Path, state: DaemonState) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def run_daemon(trainer: Trainer, n_cycles: int, ledger_path: Path,
               state_path: Path, log: Callable = print) -> DaemonState:
    """Pipelined loop: while train(N) runs on the GPU worker, prepare_data(N+1)
    runs on the CPU worker. On any failure: persist state (next_cycle unchanged
    -> a rerun resumes at the failed cycle) and re-raise (crash isolation is
    per-run; a broken trainer must not silently spin)."""
    state = load_state(state_path)
    first = state.next_cycle
    last = first + n_cycles
    with ThreadPoolExecutor(max_workers=2) as pool:
        data_future = pool.submit(trainer.prepare_data, first)
        for cycle in range(first, last):
            try:
                data_path = data_future.result()
                train_future = pool.submit(trainer.train, data_path, cycle)
                if cycle + 1 < last:  # producer-consumer overlap
                    data_future = pool.submit(trainer.prepare_data, cycle + 1)
                weights = train_future.result()
                candidates = load_ledger(ledger_path)
                cand = trainer.export_and_register(weights, cycle, candidates)
                candidates.append(cand)
                save_ledger(ledger_path, candidates)
                state.completed.append(cycle)
                state.next_cycle = cycle + 1
                state.last_error = None
                save_state(state_path, state)
                log(f"daemon[{trainer.name}] cycle {cycle} complete -> {cand.id}")
            except Exception:
                state.last_error = traceback.format_exc(limit=5)
                save_state(state_path, state)
                log(f"daemon[{trainer.name}] cycle {cycle} FAILED; "
                    f"state persisted for resume")
                raise
    return state
```

- [ ] Run to pass: `uv run pytest tests/test_factory_daemon.py tests/test_experiments_append.py -q` — expected: all pass.
- [ ] Commit:

```
git add src/ptcg/factory/daemon.py tests/test_factory_daemon.py
git commit -m "feat: slice7a T11 - training daemon core with producer-consumer overlap + resume"
```

---

### Task 12: PerDeckNetTrainer + deck-parametrized data generation + [ORCH] real training cycle

INTERFACE MISMATCH RESOLVED HERE: `scripts/generate_training_data.py` is NOT deck-parametrizable today — `deck_pairings()` always iterates every deck in the candidates dir. This task adds `--decks` (explicit deck list; a single deck yields exactly its mirror pairing) and builds `PerDeckNetTrainer` on top. Per spec S8's honest framing (recorded in the trainer docstring and journal): per-deck nets are distribution coverage + infrastructure proof, NOT a rerun of the closed evaluator-quality hypothesis.

**Files:**

- Modify: `scripts/generate_training_data.py` (add `--decks`, thread through `deck_pairings`)
- Create: `src/ptcg/factory/trainers.py`
- Create: `scripts/factory_daemon.py`
- Test: `tests/test_generate_training_data.py` (extend), `tests/test_factory_trainers.py`

**Interfaces:**

- Consumes: `scripts/generate_training_data.py` CLI (`--decks --games-per-pairing --out --seed --agent`), `scripts/train_value_net.py` CLI (`--data --out --epochs`), `ptcg.factory.daemon.run_daemon`, `ptcg.factory.candidates.next_version`.
- Produces:
  - `deck_pairings(deck_dir: Path, only: list[Path] | None = None)` — `only=[deck]` -> `[(deck, deck)]`.
  - `@dataclass PerDeckNetTrainer(deck: Path, games_per_cycle: int = 300, epochs: int = 30, data_dir: Path = experiments/data/slice7a, weights_dir: Path = src/ptcg/search, priority: float = 0.3, runner: Callable = subprocess.run)`; `name = "per-deck-net"`; weights naming `value_net_weights_<deck-slug>.json`; registered candidate `<deck-slug>-searchnet v<next>` with `provenance="daemon:per-deck-net:cycle<N>"`.
  - CLI `scripts/factory_daemon.py --deck <csv> [--cycles N] [--games-per-cycle N] [--epochs N]` — reads `asym_decision.json` for candidate priority when present; state at `experiments/factory/daemon_state.json`.

**Steps:**

- [ ] Extend `tests/test_generate_training_data.py` with (verify the existing file's import style first and match it):

```python
def test_deck_pairings_only_filter():
    from pathlib import Path

    from scripts.generate_training_data import deck_pairings

    one = [Path("a.csv")]
    assert deck_pairings(Path("ignored"), only=one) == [(Path("a.csv"), Path("a.csv"))]
    two = [Path("b.csv"), Path("a.csv")]  # sorted inside
    assert deck_pairings(Path("ignored"), only=two) == [
        (Path("a.csv"), Path("a.csv")),
        (Path("a.csv"), Path("b.csv")),
        (Path("b.csv"), Path("b.csv")),
    ]
```

- [ ] Write the failing test `tests/test_factory_trainers.py`:

```python
"""Tests for PerDeckNetTrainer (subprocess commands stubbed - no real training)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ptcg.factory.trainers import PerDeckNetTrainer


def _fake_runner(calls):
    def run(cmd, **kw):
        calls.append([str(c) for c in cmd])
        out = Path(cmd[cmd.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)
    return run


def test_prepare_train_register_pipeline(tmp_path):
    calls: list[list[str]] = []
    trainer = PerDeckNetTrainer(
        deck=Path("src/ptcg/decks/candidates/mega-starmie-water.csv"),
        games_per_cycle=5, epochs=2, data_dir=tmp_path / "data",
        weights_dir=tmp_path / "weights", priority=0.3,
        runner=_fake_runner(calls))

    data = trainer.prepare_data(0)
    assert data.name == "mega-starmie-water-c0.jsonl"
    gen = calls[0]
    assert "scripts/generate_training_data.py" in " ".join(gen)
    assert "--decks" in gen and "--games-per-pairing" in gen
    assert gen[gen.index("--games-per-pairing") + 1] == "5"

    weights = trainer.train(data, 0)
    assert weights.name == "value_net_weights_mega-starmie-water.json"
    train = calls[1]
    assert "scripts/train_value_net.py" in " ".join(train)
    assert train[train.index("--epochs") + 1] == "2"

    cand = trainer.export_and_register(weights, 0, [])
    assert cand.id == "mega-starmie-water-searchnet-v0.1"
    assert cand.novel_axis is True and cand.priority == 0.3
    assert cand.agent_config["net_weights"].endswith(
        "value_net_weights_mega-starmie-water.json")
    assert cand.provenance == "daemon:per-deck-net:cycle0"


def test_register_bumps_existing_version(tmp_path):
    from ptcg.factory.candidates import Candidate
    existing = [Candidate.create(name="mega-starmie-water-searchnet", version="v0.3",
                                 deck="d.csv", agent_kind="search-net")]
    trainer = PerDeckNetTrainer(
        deck=Path("src/ptcg/decks/candidates/mega-starmie-water.csv"),
        data_dir=tmp_path, weights_dir=tmp_path, runner=_fake_runner([]))
    cand = trainer.export_and_register(tmp_path / "w.json", 4, existing)
    assert cand.version == "v0.4" and cand.novel_axis is False
```

- [ ] Run to see them fail: `uv run pytest tests/test_factory_trainers.py tests/test_generate_training_data.py -q` — expected: `ModuleNotFoundError` / `TypeError: deck_pairings() got an unexpected keyword argument 'only'`.
- [ ] Modify `scripts/generate_training_data.py`: change `deck_pairings` to

```python
def deck_pairings(deck_dir: Path,
                  only: list[Path] | None = None) -> list[tuple[Path, Path]]:
    decks = sorted(Path(p) for p in only) if only else sorted(deck_dir.glob("*.csv"))
    return [(a, b) for i, a in enumerate(decks) for b in decks[i:]]
```

then add `p.add_argument("--decks", nargs="+", default=None, help="restrict pairings to these deck csvs (one deck = its mirror only)")`, thread a new `decks: list[str] | None` parameter through `run(...)` into the `deck_pairings(DECK_DIR, only=...)` call, and pass `args.decks` from `main()`. Keep every existing call path identical when `--decks` is absent (default None).
- [ ] Create `src/ptcg/factory/trainers.py`:

```python
"""Trainer implementations for the daemon. 7A occupant: PerDeckNetTrainer.

Honest framing (spec S8, recorded): value-net-quality improvements on the
mega-lucario mirror are a PROVEN dead-end for win rate (Slices 4-6). This
trainer's value is (a) proving the continuous-training infrastructure end to
end and (b) distribution coverage - all existing nets were trained exclusively
on mega-lucario mirror data, so search candidates on the other 8 decks
currently run an off-distribution net. The win-rate hypothesis lives in 7B's
policy-improvement trainer, which plugs into the same Trainer protocol."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ptcg.factory.candidates import Candidate, next_version

ROOT = Path(__file__).resolve().parents[3]


@dataclass
class PerDeckNetTrainer:
    deck: Path
    games_per_cycle: int = 300
    epochs: int = 30
    data_dir: Path = field(default_factory=lambda: ROOT / "experiments" / "data" / "slice7a")
    weights_dir: Path = field(default_factory=lambda: ROOT / "src" / "ptcg" / "search")
    priority: float = 0.3
    runner: Callable = subprocess.run
    name: str = "per-deck-net"

    @property
    def slug(self) -> str:
        return Path(self.deck).stem

    def _run(self, args: list[str]) -> None:
        proc = self.runner([sys.executable, *args], cwd=ROOT, timeout=24 * 3600)
        code = getattr(proc, "returncode", 0)
        if code != 0:
            raise RuntimeError(f"{args[0]} exited {code}")

    def prepare_data(self, cycle: int) -> Path:
        out = Path(self.data_dir) / f"{self.slug}-c{cycle}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        self._run(["scripts/generate_training_data.py",
                   "--decks", str(self.deck),
                   "--games-per-pairing", str(self.games_per_cycle),
                   "--seed", str(7 + cycle),
                   "--out", str(out)])
        return out

    def train(self, data_path: Path, cycle: int) -> Path:
        out = Path(self.weights_dir) / f"value_net_weights_{self.slug}.json"
        self._run(["scripts/train_value_net.py",
                   "--data", str(data_path),
                   "--out", str(out),
                   "--epochs", str(self.epochs)])
        return out

    def export_and_register(self, weights_path: Path, cycle: int,
                            candidates: list[Candidate]) -> Candidate:
        name = f"{self.slug}-searchnet"
        version = next_version(candidates, name)
        weights_rel = Path(weights_path)
        if weights_rel.is_absolute():
            weights_rel = weights_rel.relative_to(ROOT)
        return Candidate.create(
            name=name, version=version,
            deck=f"src/ptcg/decks/candidates/{self.slug}.csv",
            agent_kind="search-net",
            agent_config={"search_budget_ms": 200, "rollout_depth": 12,
                          "net_weights": weights_rel.as_posix()},
            provenance=f"daemon:{self.name}:cycle{cycle}",
            priority=self.priority,
            novel_axis=version == "v0.1")
```

- [ ] Create `scripts/factory_daemon.py`:

```python
"""CLI: run the continuous-training daemon with the 7A PerDeckNetTrainer."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory.daemon import run_daemon  # noqa: E402
from ptcg.factory.trainers import PerDeckNetTrainer  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--deck", required=True, help="deck csv the net is trained for")
    p.add_argument("--cycles", type=int, default=1)
    p.add_argument("--games-per-cycle", type=int, default=300)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--ledger",
                   default=str(ROOT / "experiments/factory/candidates.json"))
    p.add_argument("--state",
                   default=str(ROOT / "experiments/factory/daemon_state.json"))
    args = p.parse_args()

    priority = 0.3
    decision_path = ROOT / "experiments" / "factory" / "asym_decision.json"
    if decision_path.exists():
        doc = json.loads(decision_path.read_text(encoding="utf-8"))
        priority = 0.90 if doc.get("decision") == "positive" else 0.30

    trainer = PerDeckNetTrainer(deck=Path(args.deck),
                                games_per_cycle=args.games_per_cycle,
                                epochs=args.epochs, priority=priority)
    state = run_daemon(trainer, args.cycles, Path(args.ledger), Path(args.state))
    print(f"daemon done: completed cycles {state.completed}, "
          f"next_cycle={state.next_cycle}")


if __name__ == "__main__":
    main()
```

- [ ] Run to pass: `uv run pytest tests/test_factory_trainers.py tests/test_generate_training_data.py tests/test_experiments_append.py -q` then the full suite `uv run pytest -q` — expected: green.
- [ ] Commit the code before the real run:

```
git add scripts/generate_training_data.py src/ptcg/factory/trainers.py scripts/factory_daemon.py tests/test_factory_trainers.py tests/test_generate_training_data.py
git commit -m "feat: slice7a T12 - PerDeckNetTrainer, --decks data generation, daemon CLI"
```

- [ ] **[ORCH] ACCEPTANCE — one REAL small-scale end-to-end training cycle (success criterion 3).** Structured per background-arena-execution: the implementer's job ended at the commit above; the ORCHESTRATOR launches, in its own background shell:
  `uv run python scripts/factory_daemon.py --deck src/ptcg/decks/candidates/mega-starmie-water.csv --cycles 2 --games-per-cycle 20 --epochs 5`
  (2 cycles so the overlap actually exercises: prepare(c1) runs while train(c0) runs; ~20 mirror games of v0 self-play generate in seconds-to-minutes, 5 epochs train in under a minute). Watch the PID via `Get-Process`; do NOT dispatch a waiting subagent.
- [ ] (harvest agent, after the process exits) Verify with receipts: (a) `experiments/data/slice7a/mega-starmie-water-c0.jsonl` and `-c1.jsonl` exist and are non-empty; (b) `src/ptcg/search/value_net_weights_mega-starmie-water.json` exists and `ValueNetEvaluator.load()` accepts it (`uv run python -c "import sys; sys.path.insert(0,'src'); from ptcg.search.value_net import ValueNetEvaluator; ValueNetEvaluator.load('src/ptcg/search/value_net_weights_mega-starmie-water.json'); print('LOADS OK')"`); (c) `experiments/factory/candidates.json` gained `mega-starmie-water-searchnet-v0.1` and `-v0.2`; (d) the daemon log line ordering (or timestamps) shows prepare(c1) started before train(c0) finished — the CPU/GPU overlap receipt; (e) `experiments/factory/daemon_state.json` shows `next_cycle: 2`, `last_error: null`. Record all five receipts in the task report + a journal entry (`append_journal` or a manual `## daemon acceptance` note in `docs/writeup-notes.md`).
- [ ] Commit the artifacts:

```
git add experiments/factory/candidates.json experiments/factory/daemon_state.json src/ptcg/search/value_net_weights_mega-starmie-water.json docs/writeup-notes.md
git commit -m "feat: slice7a T12 - real per-deck training cycle: data gen -> train -> export -> candidates registered"
```

(Training data jsonl files stay untracked if they exceed comfortable repo size — check `git check-ignore experiments/data/slice7a` first; the slice4/slice6 data-dir convention applies.)

---

### Task 13: [ORCH] First real automated factory cycle (success criterion 2)

REAL submission upload, auto-approved per the spec's retired-rule decision (Purpose decisions: "no per-submission human approval ... RETIRED for this pipeline"); the versioned-identity description standard applies. Structured per background-arena-execution: implementer does pre-flight only; the ORCHESTRATOR owns the long-running cycle process.

**Files:**

- Modify (by the pipeline itself, verified after): `experiments/factory/candidates.json`, `experiments/factory/submission_counter.json`, `experiments/LADDER.md`, `experiments/EXPERIMENTS.md`, `experiments/factory/digests/`, `docs/writeup-notes.md`.

**Interfaces:**

- Consumes: the full T3-T12 pipeline + real Kaggle CLI auth.
- Produces: at least one real harvested-evaluated-gated-uploaded submission with an auto-generated versioned description and a LADDER.md row.

**Steps:**

- [ ] (implementer/pre-flight) Rehearse the FULL pipeline with zero uploads: `uv run python scripts/factory_cycle.py --no-submit --games 50 --max-candidates 2` run in the foreground with output captured. Verify: harvest pulled real submission rows; the counter reconciled; 1-2 candidates evaluated with EXPERIMENTS.md rows appended; the gate produced dry-run decisions with correctly-templated descriptions; a digest + journal entry were written; REAL bundles were built AND verify+smoke passed (this rehearsal exercises the T6 real-bundle path the T9 unit tests stubbed). Report the dry-run digest contents and STOP.
- [ ] (orchestrator) Review the rehearsal digest for sanity (descriptions well-formed, gate reasons sensible, counter arithmetic consistent with the harvested rows). Fix-loop any anomaly before the real run.
- [ ] (orchestrator) Launch the real cycle in an orchestrator-owned background shell: `uv run python scripts/factory_cycle.py --games 150 --max-candidates 2`. Expected wall time: heuristic candidates evaluate in ~1 min; a search-net candidate at 150 games costs ~5-6 min; bundle smoke adds seconds-to-minutes. Watch the PID; harvest from the log/digest after exit.
- [ ] (harvest agent) Verify with receipts, not the log's word: (a) `uvx kaggle competitions submissions -c pokemon-tcg-ai-battle` shows the new submission with the auto-generated description (`<name> <version> | deck=... | agent=... | local_wr=... | <commit> | factory`), status PENDING/COMPLETE; (b) `experiments/LADDER.md` has the row; (c) the counter file incremented; (d) the ledger candidate is `submitted`; (e) EXPERIMENTS.md rows exist for each evaluated candidate. If the gate correctly decided NOT to submit anything (all below incumbent), that is a legitimate outcome for the GATE but not for this task's success criterion — in that case re-run with the next-best queued candidate or (if the queue is genuinely exhausted above the floor) record the exploration-exception path by letting a novel-axis candidate through; the criterion is one real harvest->evaluate->gate->upload->ledger-row pass.
- [ ] (harvest agent) Append a journal entry naming what was submitted and why (gate reason verbatim), then commit:

```
git add experiments/factory/candidates.json experiments/factory/submission_counter.json experiments/LADDER.md experiments/EXPERIMENTS.md experiments/factory/digests docs/writeup-notes.md
git commit -m "feat: slice7a T13 - first real automated factory cycle: harvest, evaluate, gate, upload, ledger"
```

- [ ] (orchestrator, after the real cycle is verified) Register the nightly task: `powershell -ExecutionPolicy Bypass -File scripts/register_factory_task.ps1` — confirm with `Get-ScheduledTask -TaskName ptcg-factory-nightly`.

---

### Task 14: Finalization (combined-review eligible)

**Files:**

- Create: `tests/test_factory_coherence.py`
- Create: `docs/weekly-review-checklist.md`
- Modify: `CLAUDE.md` (Slice-7A status paragraph, ASCII-clean)
- Modify: `docs/writeup-notes.md` (seed the slice's methodology decisions)

**Interfaces:**

- Consumes: everything shipped in T1-T13.
- Produces: a repo-state coherence test that runs on the REAL ledgers; operator docs; the updated project memory paragraph.

**Steps:**

- [ ] Write `tests/test_factory_coherence.py` (runs against the real committed ledgers — success criterion 4's standing guard):

```python
"""Coherence checks over the REAL factory ledgers (run on every suite pass)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ptcg.factory.candidates import load_ledger, make_id, parse_version

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "experiments" / "factory" / "candidates.json"


@pytest.mark.skipif(not LEDGER.exists(), reason="factory ledger not seeded yet")
def test_real_ledger_is_coherent():
    cands = load_ledger(LEDGER)
    assert cands, "ledger exists but is empty"
    ids = [c.id for c in cands]
    assert len(ids) == len(set(ids)), f"duplicate candidate ids: {ids}"
    for c in cands:
        assert c.id == make_id(c.name, c.version)
        parse_version(c.version)  # raises on malformed versions
        assert (ROOT / c.deck).exists(), f"{c.id}: deck missing {c.deck}"
        assert 0.0 <= c.priority <= 1.0
        if c.agent_kind == "search-net":
            weights = c.agent_config.get("net_weights")
            assert weights, f"{c.id}: search-net without net_weights"
            assert (ROOT / weights).exists(), f"{c.id}: weights missing {weights}"
        if c.local_wr is not None:
            assert 0.0 <= c.local_wr <= 1.0
        if c.submitted_at is not None:
            assert c.status.value in ("submitted", "scored", "retired")
```

- [ ] Run: `uv run pytest tests/test_factory_coherence.py -q` — expected: pass against the real post-T13 ledger (fix any incoherence it finds at the SOURCE, not by loosening the test).
- [ ] Create `docs/weekly-review-checklist.md` (ASCII-clean):

```markdown
# Weekly Factory Review Checklist (spec S1: the weekly review is the brain)

Run this as a Claude session in the repo, roughly weekly.

1. Read state: latest digests (experiments/factory/digests/), experiments/LADDER.md
   score trajectories, experiments/factory/candidates.json queue.
2. Reconcile: does the local-WR ordering match the ladder-score ordering? Divergence
   is signal - the ladder is the evaluator of record; note it in writeup-notes.md.
3. Re-prioritize: adjust queue priorities from ladder evidence (hand-edit the ledger
   or bump via a short script; record the reasoning in writeup-notes.md).
4. Author candidates: deck tweaks (new version of an existing name) and/or agent
   config variants; add via ptcg.factory.candidates + save_ledger; novel axes get
   novel_axis=true.
5. Retire dead ends: mark clearly-dominated candidates retired with a notes reason.
6. Daemon check: experiments/factory/daemon_state.json last_error null? Registered
   candidates flowing into the queue?
7. Journal: one strategic-prose entry in docs/writeup-notes.md (what the ladder
   taught us this week, what we change in response).
8. Housekeeping: counter file sane vs kaggle submissions list; PAUSE file absent;
   nightly task last-run result (Get-ScheduledTaskInfo ptcg-factory-nightly).
```

- [ ] Seed `docs/writeup-notes.md` with the slice's methodology decisions (one `append_journal`-style section, or hand-written under the same header format): ladder-as-evaluator rationale, asymmetric-test design + decision (from T2), gate policy (incumbent/exploration/cadence/cap), per-deck-net honest framing, retired approval rule -> description standard + digest + pause.
- [ ] Add the Slice-7A status paragraph to `CLAUDE.md` (ASCII-clean, following the Slice 4-6 paragraph pattern): what shipped (factory stages + daemon), the asymmetric-test result and its recorded decision verbatim, the first real automated submission (id + description), what stays manual (weekly review), and the 7B hook (PolicyImprovementTrainer behind the same Trainer protocol).
- [ ] Full-suite + slow gates: `uv run pytest -q` then `uv run pytest -m slow -q` — expected: green. Report measured counts, not bare PASS.
- [ ] Sweep every checkbox in this plan file; reconcile any task that diverged (note plan-drift resolutions inline in plan.md's Progress Log).
- [ ] Commit:

```
git add tests/test_factory_coherence.py docs/weekly-review-checklist.md CLAUDE.md docs/writeup-notes.md
git commit -m "docs: slice7a T14 - coherence guard, weekly-review checklist, CLAUDE.md status"
```

---

## Self-review (performed at plan-write time)

- **Spec coverage:** every spec section maps to a task (see the coverage table above); the S5 episode-data investigation is an explicit T7 step; S10's out-of-scope items are honored (reprioritize is a fixed-delta rule, not an optimizer; no auto-evolved decks; no 7B internals).
- **Placeholder scan:** no TBDs; every test and implementation block is complete code; repeated patterns (atomic write, utf-8) are repeated rather than referenced.
- **Interface consistency:** `Candidate.create(name=, version=, deck=, agent_kind=, ...)`, `Status.BELOW_INCUMBENT`, `save_ledger(path, candidates)`, `EvalConfig`, `SubmissionCounter.today_count/record/reconcile`, `GateDecision.mark_below_incumbent`, `FakeKaggleClient.submitted`, `run_cycle(paths, client, eval_cfg, ...)`, `Trainer.prepare_data/train/export_and_register`, `next_version` — names checked for exact agreement across T3-T13.
- **Arithmetic:** all plan-authored vectors executably verified 2026-07-11 (diff CI 0.0033155/0.1966845; flat half-width 0.0800167; priorities 0.6/0.4; gate margins 0.07/0.02; reprioritize 0.55; floor cases 0.6/0.4 vs 0.45).
- **Complexity glance:** all loops O(n) at n < 10^4; `parse_experiments` is O(lines); no O(n^2) shapes. First production-scale runs (T12 daemon cycle, T13 real cycle) are explicitly treated as tests.
- **Known risks named:** (1) real Kaggle CSV headers may differ — T7's investigation step runs BEFORE the parser is finalized; (2) the search-bundle both-seats smoke could be slow — T6 measures it and reports plan-drift rather than silently raising the timeout; (3) T13's gate may legitimately submit nothing — the task defines the fallback path explicitly.
