"""Local evaluation runner: sanity filter, not gatekeeper (spec S3).

Runs top-priority queued candidates vs a set of fixed baselines (heuristic-v0
piloting each configured baseline deck) inside a wall-clock budget, pooling
results across baselines into a single pass/fail win rate. Per-candidate
crash isolation: a failed series marks the candidate back to queued with an
error note and the queue continues; the ledger is saved after every
transition so partial results always persist.

Dual/multi-baseline rationale (weekly-review recalibration): grading every
candidate solely against mega-lucario-fighting inverted local ordering vs the
ladder (the starmie deck family is the ladder's best performer but was
retired locally at 0.433 because non-lucario decks were graded only on their
lucario matchup). Pooling wins across multiple baselines is a durable fix -
baselines are a configurable list, not a hardcoded pair.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ptcg.agents.heuristic import HeuristicAgent
from ptcg.arena.stats import SeriesStats
from ptcg.factory.candidates import Candidate, Status

ROOT = Path(__file__).resolve().parents[3]
BASELINE_DECK = ROOT / "src" / "ptcg" / "decks" / "candidates" / "mega-lucario-fighting.csv"
STARMIE_BASELINE_DECK = ROOT / "src" / "ptcg" / "decks" / "candidates" / "mega-starmie-water.csv"
DEFAULT_BASELINES: tuple[Path, ...] = (BASELINE_DECK, STARMIE_BASELINE_DECK)

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
    baselines: list[Path] = field(default_factory=lambda: list(DEFAULT_BASELINES))
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
            c_uct=float(cfg.get("c_uct", 1.4)),
            max_depth=int(cfg.get("max_depth", 40)),
            rollout_depth=int(cfg.get("rollout_depth", 12)),
            deviate_min_visits=int(cfg.get("deviate_min_visits", 20)),
            deviate_value_edge=float(cfg.get("deviate_value_edge", 0.12)),
            deviate_min_visit_frac=float(cfg.get("deviate_min_visit_frac", 0.0)),
            final_move_rule=str(cfg.get("final_move_rule", "most_visited")),
            robust_min_visits=int(cfg.get("robust_min_visits", 5)),
            use_root_prior=bool(cfg.get("use_root_prior", False)),
            prior_tau=float(cfg.get("prior_tau", 0.1)),
            c_puct=float(cfg.get("c_puct", 1.5)),
        )
        tm = TimeManager(total_s=1e9,
                         max_move_s=float(cfg.get("search_budget_ms", 200)) / 1000.0)
        agent_kwargs: dict[str, Any] = dict(config=sc, time_manager=tm)
        if "net_weights" in cfg:
            agent_kwargs["evaluator"] = ValueNetEvaluator.load(ROOT / cfg["net_weights"])
        agent = SearchAgent(deck, **agent_kwargs)
    elif candidate.agent_kind == "search-policy":
        from ptcg.agents.search_agent import SearchAgent
        from ptcg.search.searcher import SearchConfig
        from ptcg.search.timing import TimeManager
        from ptcg.search.value_net import ValueNetEvaluator
        cfg = candidate.agent_config
        gate_off = cfg.get("gate", "off") == "off"
        sc = SearchConfig(
            rollout_depth=int(cfg.get("rollout_depth", 0)),
            deviate_min_visits=0 if gate_off else int(cfg.get("deviate_min_visits", 20)),
            deviate_value_edge=0.0 if gate_off else float(cfg.get("deviate_value_edge", 0.12)),
            use_tree_prior=bool(cfg.get("tree_prior", False)),
            policy_opponent=bool(cfg.get("policy_opponent", False)),
            policy_rollout=bool(cfg.get("policy_rollout", False)),
        )
        tm = TimeManager(total_s=1e9,
                         max_move_s=float(cfg.get("search_budget_ms", 200)) / 1000.0)
        agent = SearchAgent(deck, config=sc, time_manager=tm,
                            evaluator=ValueNetEvaluator.load(ROOT / cfg["net_weights"]),
                            policy_weights=ROOT / cfg["policy_weights"])
    else:
        raise ValueError(f"unknown agent_kind {candidate.agent_kind!r}")
    agent.name = candidate.id
    return agent


def split_games(total: int, n_baselines: int) -> list[int]:
    """Split `total` games evenly across `n_baselines`, keeping the overall
    per-candidate game count unchanged (nightly cycle time budget is fixed -
    see .claude/rules/time-budgeted-arena-contention.md).

    Integer division with the remainder distributed one-at-a-time to the
    FIRST `total % n_baselines` baselines in list order (so earlier-listed
    baselines may get one extra game each). `sum(split_games(total, n)) ==
    total` always holds, including when `total < n_baselines` (some
    baselines get 0 games that cycle).
    """
    if n_baselines <= 0:
        raise ValueError("need at least one baseline")
    base, remainder = divmod(total, n_baselines)
    return [base + (1 if i < remainder else 0) for i in range(n_baselines)]


@dataclass
class BaselineResult:
    """Per-baseline breakdown for a single candidate eval (design constraint 4)."""
    baseline: str
    wins: int
    games: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


@dataclass
class PooledSeriesStats(SeriesStats):
    """A SeriesStats pooled across one or more baselines, plus the per-baseline
    breakdown that produced it. Pooling is a straight sum of wins/draws/game
    timings across baselines, so `win_rate_a`/`n`/`markdown_row` (inherited
    from SeriesStats) all operate on the POOLED totals - the 0.45 floor is
    applied to this pooled win rate, never a per-baseline one.
    """
    breakdown: list[BaselineResult] = field(default_factory=list)

    def breakdown_text(self) -> str:
        return "; ".join(
            f"{b.baseline}: {b.wins}/{b.games} ({b.win_rate:.3f})" for b in self.breakdown
        )


def _real_series(candidate: Candidate, cfg: EvalConfig) -> PooledSeriesStats:
    from ptcg.arena.runner import load_deck, run_series
    deck_a = load_deck(ROOT / candidate.deck)
    counts = split_games(cfg.games, len(cfg.baselines))
    pooled = PooledSeriesStats()
    for baseline_deck, n_games in zip(cfg.baselines, counts):
        if n_games == 0:
            continue
        deck_b = load_deck(baseline_deck)
        stats = run_series(build_agent(candidate, deck_a), HeuristicAgent(),
                           deck_a, deck_b, n_games)
        pooled.wins_a += stats.wins_a
        pooled.wins_b += stats.wins_b
        pooled.draws += stats.draws
        pooled.game_seconds.extend(stats.game_seconds)
        pooled.max_move_seconds = max(pooled.max_move_seconds, stats.max_move_seconds)
        pooled.breakdown.append(BaselineResult(
            baseline=Path(baseline_deck).stem, wins=stats.wins_a, games=stats.n))
    return pooled


def append_experiments_row(cand: Candidate, stats, experiments_md: Path,
                           date: str | None = None) -> None:
    experiments_md = Path(experiments_md)
    breakdown = getattr(stats, "breakdown", None)
    baseline_label = "+".join(b.baseline for b in breakdown) if breakdown else BASELINE_DECK.name
    row = stats.markdown_row(
        date or dt.date.today().isoformat(), cand.id, "heuristic-v0",
        Path(cand.deck).name, baseline_label,
        f"slice7a-factory-eval {cand.id}")
    if experiments_md.exists():
        text = experiments_md.read_text(encoding="utf-8")
    else:
        experiments_md.parent.mkdir(parents=True, exist_ok=True)
        text = EXPERIMENTS_HEADER
    experiments_md.write_text(text + row + "\n", encoding="utf-8")


_EVAL_TAG = "eval: "
_EVAL_SEP = " | " + _EVAL_TAG


def _strip_eval_segment(notes: str) -> str:
    """Return the human-authored prefix of `notes`, stripping any
    eval-generated segment a prior `evaluate_queued()` cycle appended (F2 fix:
    authored candidate notes - deck rationale, smoke-test records - must
    survive across eval and must never accumulate across re-evaluations).

    An eval segment is recognizable by the `_EVAL_SEP` (" | eval: ") marker
    when authored notes precede it, or by a leading `_EVAL_TAG` ("eval: ")
    when there were no authored notes at all. If neither marker is present,
    `notes` is entirely authored text and is returned unchanged.
    """
    notes = notes or ""
    idx = notes.find(_EVAL_SEP)
    if idx != -1:
        return notes[:idx]
    if notes.startswith(_EVAL_TAG):
        return ""
    return notes


def _compose_notes(authored: str, eval_text: str) -> str:
    """Join authored notes with fresh eval-generated text using the marker
    `_strip_eval_segment` reverses on the next cycle. No eval segment is
    appended when `eval_text` is empty, so authored notes pass through
    untouched (this is also what keeps the duck-typed breakdown seam intact:
    a plain SeriesStats stub with no `.breakdown_text()` produces an empty
    eval_text on a pass, leaving `authored` as-is).
    """
    if not eval_text:
        return authored
    if authored:
        return f"{authored}{_EVAL_SEP}{eval_text}"
    return f"{_EVAL_TAG}{eval_text}"


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
        # F2 fix: strip any eval segment a PRIOR cycle appended before this
        # cycle writes its own - authored notes (and only authored notes)
        # survive into every branch below, and re-eval never stacks.
        authored_notes = _strip_eval_segment(cand.notes)
        try:
            stats = series_fn(cand, cfg)
        except Exception as exc:  # crash isolation (spec S9): persist, resume queue
            cand.status = Status.QUEUED
            eval_text = f"eval-error: {exc!r}"[:300]
            cand.notes = _compose_notes(authored_notes, eval_text)
            save_fn(ledger_path, candidates)
            continue
        cand.local_wr = stats.win_rate_a
        cand.local_games = stats.n
        # Structured per-baseline breakdown: the same already-computed
        # BaselineResult objects the notes fragment is built from (no new
        # computation), recorded as plain dicts so they round-trip through the
        # JSON ledger and the digest can render them (factory-polish change 2).
        # Duck-typed like breakdown_text below: a bare SeriesStats stub has no
        # `.breakdown`, and an empty breakdown means nothing to record -> None.
        breakdown = getattr(stats, "breakdown", None)
        cand.local_breakdown = ([
            {"baseline": b.baseline, "wins": b.wins, "games": b.games}
            for b in breakdown
        ] if breakdown else None)
        if stats.win_rate_a < cfg.floor:
            cand.status = Status.RETIRED
            status_msg = f"below {cfg.floor:.2f} floor vs baseline"
        else:
            cand.status = Status.EVALUATED
            status_msg = ""
        # Per-baseline breakdown (design constraint 4): only PooledSeriesStats
        # (the real multi-baseline series_fn) carries `.breakdown_text()` -
        # a bare SeriesStats stub (as used by most unit tests / cycle.py test
        # doubles) has none, so this is a no-op there and eval_text falls
        # back to status_msg (possibly empty) unchanged.
        breakdown_text = stats.breakdown_text() if hasattr(stats, "breakdown_text") else ""
        if breakdown_text:
            eval_text = f"{status_msg} | {breakdown_text}" if status_msg else breakdown_text
        else:
            eval_text = status_msg
        cand.notes = _compose_notes(authored_notes, eval_text)
        save_fn(ledger_path, candidates)
        if append_row is not None:
            append_row(cand, stats)
        done.append(cand)
    return done
