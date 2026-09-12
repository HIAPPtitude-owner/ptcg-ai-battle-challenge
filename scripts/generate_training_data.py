"""Generate training data: v0/search self-play for the value net, or --policy-targets search self-play emitting root visit-distribution rows for the policy net."""
from __future__ import annotations

import argparse
import random
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
from ptcg.search.value_net import ValueNetEvaluator  # noqa: E402
from ptcg.train.policy_targets import (PolicyRecordingAgent,  # noqa: E402
                                       label_policy_records)
from ptcg.train.trajectory import (DeviationTracker, RecordingAgent,  # noqa: E402
                                   append_jsonl, label_records)

DECK_DIR = ROOT / "src" / "ptcg" / "decks" / "candidates"
V2_WEIGHTS = ROOT / "src" / "ptcg" / "search" / "value_net_weights_v2.json"


def deck_pairings(deck_dir: Path,
                  only: list[Path] | None = None) -> list[tuple[Path, Path]]:
    decks = sorted(Path(p) for p in only) if only else sorted(deck_dir.glob("*.csv"))
    return [(a, b) for i, a in enumerate(decks) for b in decks[i:]]


def make_agents(agent_kind: str, deck_a: list[int], deck_b: list[int],
                budget_ms: int, gate: str, sink: list, tracker):
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


def make_policy_target_agents(deck_a: list[int], deck_b: list[int],
                              budget_ms: int, sink: list, args):
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


def run(games_per_pairing: int, out: Path, seed: int, agent_kind: str,
        budget_ms: int, gate: str, log_every: int = 200,
        decks: list[str] | None = None, policy_targets: bool = False,
        args=None) -> None:
    only = [Path(d) for d in decks] if decks else None
    pairings = deck_pairings(DECK_DIR, only=only)
    rng = random.Random(seed)  # reserved for future sampling knobs; order is fixed
    game_id = 0
    errors = 0
    src = "search" if agent_kind == "search" else "v0"
    t0 = time.perf_counter()
    for deck_a_path, deck_b_path in pairings:
        deck_a, deck_b = load_deck(deck_a_path), load_deck(deck_b_path)
        for g in range(games_per_pairing):
            sink: list = []
            if policy_targets:
                a0, a1 = make_policy_target_agents(deck_a, deck_b, budget_ms,
                                                   sink, args)
            else:
                tracker = DeviationTracker()
                a0, a1 = make_agents(agent_kind, deck_a, deck_b, budget_ms, gate,
                                     sink, tracker)
            # alternate seats like run_series does
            if g % 2 == 0:
                result = play_match(a0, a1, deck_a, deck_b)
            else:
                result = play_match(a0, a1, deck_b, deck_a)
            if result.error is not None:
                errors += 1
                continue
            if policy_targets:
                append_jsonl(label_policy_records(sink, result.winner, game_id), out)
            else:
                append_jsonl(label_records(sink, result.winner, game_id, src=src), out)
            game_id += 1
            if game_id % log_every == 0:
                rate = game_id / (time.perf_counter() - t0)
                print(f"{game_id} games ({rate:.1f}/s), {errors} errors", flush=True)
    print(f"DONE: {game_id} games, {errors} errors, "
          f"{time.perf_counter() - t0:.0f}s -> {out}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--games-per-pairing", type=int, default=300)
    p.add_argument("--out", default=None,
                   help="defaults to experiments/data/slice4/train_v1.jsonl, or "
                        "experiments/data/slice7b/policy_gen1.jsonl with --policy-targets")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--agent", choices=["v0", "search"], default="v0")
    p.add_argument("--search-budget-ms", type=int, default=200)
    p.add_argument("--gate", choices=["on", "off"], default="off")
    p.add_argument("--decks", nargs="+", default=None,
                   help="restrict pairings to these deck csvs (one deck = its mirror only)")
    p.add_argument("--measure", action="store_true",
                   help="run 20 games on one pairing, print s/game estimate, exit")
    p.add_argument("--policy-targets", action="store_true",
                   help="record per-decision root visit distributions (expert-iteration "
                        "data-gen mode) instead of win/loss value-net rows; requires "
                        "--agent search and --gate off")
    p.add_argument("--policy-weights", default=None,
                   help="gen-2+: inject a trained policy net (unused in gen-1)")
    p.add_argument("--tree-prior", action="store_true")
    p.add_argument("--policy-opponent", action="store_true")
    p.add_argument("--policy-rollout", action="store_true")
    args = p.parse_args(argv)
    if args.policy_targets:
        if args.agent != "search":
            raise SystemExit("--policy-targets requires --agent search")
        if args.gate != "off":
            raise SystemExit(
                "policy-targets requires --gate off: the search's move must be "
                "played or trajectories stay on v0's distribution")
        if args.out is None:
            args.out = "experiments/data/slice7b/policy_gen1.jsonl"
    elif args.out is None:
        args.out = "experiments/data/slice4/train_v1.jsonl"
    if args.measure:
        if args.agent == "search":
            out = ROOT / "experiments" / "data" / "slice6" / "_measure.jsonl"
        else:
            out = ROOT / "experiments" / "data" / "slice4" / "_measure.jsonl"
        deck = load_deck(deck_pairings(DECK_DIR)[0][0])
        t0 = time.perf_counter()
        for gid in range(20):
            sink: list = []
            tracker = DeviationTracker()
            a0, a1 = make_agents(args.agent, deck, deck, args.search_budget_ms,
                                 args.gate, sink, tracker)
            r = play_match(a0, a1, deck, deck)
            if r.error is None:
                src = "search" if args.agent == "search" else "v0"
                append_jsonl(label_records(sink, r.winner, gid, src=src), out)
        per = (time.perf_counter() - t0) / 20
        total = per * 45 * args.games_per_pairing
        if args.agent == "search":
            print(f"{per:.2f}s/game -> est {total / 3600:.1f} h "
                  f"for 45x{args.games_per_pairing} games")
        else:
            print(f"{per:.2f}s/game -> est {total / 60:.0f} min "
                  f"for 45x{args.games_per_pairing} games")
        return
    run(args.games_per_pairing, ROOT / args.out, args.seed, args.agent,
        args.search_budget_ms, args.gate, decks=args.decks,
        policy_targets=args.policy_targets, args=args)


if __name__ == "__main__":
    main()
