"""Seed the initial ~13 factory candidates (spec S2): the 9 Slice-3 decks on
heuristic-v0 plus search-net variants on the top 3 decks and one root-PUCT-prior
config. Heuristic priorities = Slice-3 tournament pooled WRs (computed from the
ledger, never hardcoded); search priorities = the T2 asymmetric-test decision.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.factory.candidates import (  # noqa: E402
    Candidate,
    ledger_lock,
    load_ledger,
    save_ledger,
)

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


def _seed_ledger(ledger_path: Path, seeds: list[Candidate]) -> tuple[list[Candidate], list[Candidate]]:
    """Append-new-only merge, under the SAME cross-process lock the training
    daemon (daemon.py) and factory cycles (cycle.py) hold during their own
    load_ledger -> mutate -> save_ledger sequences (see candidates.py's
    merge_save docstring for the lost-update race this closes).

    Deliberately NOT `merge_save`: merge_save REPLACES any on-disk entry
    whose id matches one of ours, which would silently wipe out evaluation
    progress (status/local_wr/score_history) on a candidate this script
    already seeded in a prior run. We only ever want to ADD ids that are not
    yet present and otherwise leave disk-only entries (including anything
    the daemon appended concurrently) completely untouched.
    """
    with ledger_lock(ledger_path):
        existing = load_ledger(ledger_path)
        existing_ids = {c.id for c in existing}
        added = [c for c in seeds if c.id not in existing_ids]
        save_ledger(ledger_path, existing + added)
    return existing, added


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
    existing, added = _seed_ledger(ledger_path, seeds)
    print(f"seeded {len(added)} new candidates ({len(existing)} already present) "
          f"-> {ledger_path}")
    for c in added:
        print(f"  {c.id:44s} priority={c.priority:.3f} novel_axis={c.novel_axis}")


if __name__ == "__main__":
    main()
