"""CLI: run an agent-vs-agent series and log it to experiments/EXPERIMENTS.md."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402
from ptcg.agents.random_agent import RandomAgent  # noqa: E402
from ptcg.agents.search_agent import SearchAgent  # noqa: E402
from ptcg.arena.runner import load_deck, run_series  # noqa: E402
from ptcg.arena.search_metrics import format_note, summarize  # noqa: E402
from ptcg.search.searcher import SearchConfig  # noqa: E402
from ptcg.search.timing import TimeManager  # noqa: E402
from ptcg.search.value_net import ValueNetEvaluator  # noqa: E402


def build_search_config(args) -> SearchConfig:
    return SearchConfig(
        rollout_depth=args.rollout_depth,
        deviate_min_visits=args.deviate_min_visits,
        deviate_value_edge=args.deviate_value_edge,
        deviate_min_visit_frac=args.deviate_min_visit_frac,
        final_move_rule=args.final_move_rule,
        use_root_prior=args.root_prior,
        prior_tau=args.prior_tau,
        c_puct=args.c_puct,
        use_tree_prior=args.tree_prior,
        policy_opponent=args.policy_opponent,
        policy_rollout=args.policy_rollout,
    )


def _time_manager(args):
    total = 480.0 if args.real_clock else 1e9
    return TimeManager(total_s=total, max_move_s=args.search_budget_ms / 1000.0)


V2_WEIGHTS = ROOT / "src" / "ptcg" / "search" / "value_net_weights_v2.json"

AGENTS = {
    "random": lambda deck, a: RandomAgent(seed=0),
    "heuristic": lambda deck, a: HeuristicAgent(),
    "search": lambda deck, a: SearchAgent(
        deck, config=build_search_config(a), time_manager=_time_manager(a),
        collect_stats=a.collect_stats),
    "search-net": lambda deck, a: SearchAgent(
        deck, config=build_search_config(a), time_manager=_time_manager(a),
        collect_stats=a.collect_stats,
        evaluator=(ValueNetEvaluator.load(a.net_weights) if a.net_weights
                   else ValueNetEvaluator.load_default())),
    "search-policy": lambda deck, a: SearchAgent(
        deck, config=build_search_config(a), time_manager=_time_manager(a),
        collect_stats=a.collect_stats,
        evaluator=(ValueNetEvaluator.load(a.net_weights) if a.net_weights
                   else ValueNetEvaluator.load(V2_WEIGHTS)),
        policy_weights=a.policy_weights),
}


def _apply_net_weights_name(agent, kind: str, args) -> None:
    """When --net-weights is set and `kind` is the search-net CLI choice,
    rename the constructed agent so EXPERIMENTS.md rows distinguish weight
    variants, e.g. value_net_weights_v2.json -> search-net-v2."""
    if kind == "search-net" and args.net_weights:
        stem = Path(args.net_weights).stem.replace("value_net_weights_", "")
        agent.name = f"search-net-{stem}"


def _apply_policy_weights_name(agent, kind: str, args) -> None:
    """When --policy-weights is set and `kind` is the search-policy CLI
    choice, append the weights-file generation stem to the constructed
    agent's name so EXPERIMENTS.md rows distinguish policy-net generations,
    e.g. policy_net_weights_mega-lucario-fighting_gen1.json ->
    search-policy-mega-lucario-fighting_gen1."""
    if kind == "search-policy" and args.policy_weights:
        stem = Path(args.policy_weights).stem.replace("policy_net_weights_", "")
        agent.name = f"search-policy-{stem}"


def _write_json_collision_free(path: Path, doc: dict) -> Path:
    """Write `doc` as JSON to `path`, or to a `-2`/`-3`/... suffixed sibling if
    `path` is already taken. Uses exclusive create (`"x"` mode) so concurrent
    writers racing on the same filename never overwrite each other's file —
    a check-then-write (`path.exists()` then `write_text`) would still have a
    TOCTOU race between two processes started at the same instant."""
    stem, suffix = path.stem, path.suffix
    candidate = path
    n = 2
    while True:
        try:
            with candidate.open("x", encoding="utf-8") as f:
                json.dump(doc, f, indent=2)
            return candidate
        except FileExistsError:
            candidate = path.with_name(f"{stem}-{n}{suffix}")
            n += 1


def write_sidecar(path: Path, label: str, summary, per_game_counts: list[int],
                  max_move_s: float) -> Path:
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
    return _write_json_collision_free(path, doc)


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
    p.add_argument("--root-prior", action="store_true")
    p.add_argument("--prior-tau", type=float, default=0.1)
    p.add_argument("--c-puct", type=float, default=1.5)
    p.add_argument("--collect-stats", action="store_true")
    p.add_argument("--real-clock", action="store_true")
    p.add_argument("--net-weights", default=None)
    p.add_argument("--policy-weights", default=None)
    p.add_argument("--tree-prior", action="store_true")
    p.add_argument("--policy-opponent", action="store_true")
    p.add_argument("--policy-rollout", action="store_true")
    args = p.parse_args()

    deck_a, deck_b = load_deck(args.deck_a), load_deck(args.deck_b)
    agent_a = AGENTS[args.agent_a](deck_a, args)
    agent_b = AGENTS[args.agent_b](deck_b, args)
    _apply_net_weights_name(agent_a, args.agent_a, args)
    _apply_net_weights_name(agent_b, args.agent_b, args)
    _apply_policy_weights_name(agent_a, args.agent_a, args)
    _apply_policy_weights_name(agent_b, args.agent_b, args)

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
        sidecar_path = write_sidecar(
            ROOT / "experiments" / "instrumentation" / f"{date}-{safe}.json",
            args.notes, summary, per_game_counts, stats.max_move_seconds)
        print(f"instrumentation: {format_note(summary)} -> {sidecar_path}")

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


if __name__ == "__main__":
    main()
