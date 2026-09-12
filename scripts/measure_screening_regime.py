"""Read-only measurement of the current pool's composition/WR landscape
(spec §3) — the input receipt for the T6 Brad gate (cull thresholds +
FLOOR_BAR/ANCHOR_BAR proposals).

Produces, into --out-dir (default experiments/):
  (a) composition histograms (energy/pokemon/trainer) of the ACTIVE pool,
      computed DIRECTLY from each canonical deck's cards (works pre- and
      post-column-migration; no dependency on the backfill);
  (b) stratified WR-vs-anchor: energy-count quartile strata, N decks per
      stratum x --games in-process games each, run --runs times
      (default 2, .claude/rules/stochastic-gate-replication.md — ALL runs
      reported, never just the friendlier one);
  (c) the current baseline lineage's WR-vs-anchor (--baseline-games per
      run) — the ANCHOR_BAR input (spec §5: baseline WR plus a margin).

Disk/lock discipline: the DB is opened READ-ONLY via a mode=ro URI — no
copy is made (thin disk margin) and no write lock is ever taken, so the
24/7 workers are unaffected. Games are played in-process
(HeuristicAgent both sides, ~0.02s/game measured 2026-08-04).

Deliberately does NOT import scripts.measure_floor_distribution (its
module-level reseed TEMPLATES are <8-basic decks that now fail loud);
the per-game loop below mirrors its play_series_vs_anchor
(measure_floor_distribution.py:123-172) instead.

Usage:
    uv run python scripts/measure_screening_regime.py --db "experiments/factory/tournament.db"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402
from ptcg.arena.runner import play_match  # noqa: E402
from ptcg.factory import anchor, loop_state  # noqa: E402
from ptcg.factory.builder import composition_counts  # noqa: E402

_ACTIVE_DECKS_QUERY = (
    "SELECT c.id AS concept_id, d.id AS deck_id, d.cards AS cards "
    "FROM concepts c JOIN decks d ON d.concept_id = c.id "
    "AND d.shell_variant = (SELECT MIN(d2.shell_variant) FROM decks d2 "
    "WHERE d2.concept_id = c.id) "
    "WHERE c.status = 'active'"
)


def _connect_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def energy_quartile_bounds(energies: list[int]) -> list[int]:
    """[q1, q2, q3] as sorted-list index cuts (n//4, n//2, 3n//4)."""
    s = sorted(energies)
    n = len(s)
    return [s[n // 4], s[n // 2], s[(3 * n) // 4]]


def stratum_of(e: int, bounds: list[int]) -> int:
    q1, q2, q3 = bounds
    if e <= q1:
        return 0
    if e <= q2:
        return 1
    if e <= q3:
        return 2
    return 3


def _anchor_cards() -> list[int]:
    return [
        int(line)
        for line in anchor.ANCHOR_DECK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def play_series_vs_anchor(
    deck_cards: list[int], anchor_cards: list[int], n_games: int, label: str
) -> dict:
    """Mirrors measure_floor_distribution.play_series_vs_anchor
    (measure_floor_distribution.py:123-172): HeuristicAgent both sides,
    alternating who is player 0 each game. Returns win/draw/loss from
    deck_cards' perspective."""
    deck_agent = HeuristicAgent()
    anchor_agent = HeuristicAgent()
    wins = draws = losses = 0
    for g in range(n_games):
        if g % 2 == 0:
            r = play_match(deck_agent, anchor_agent, deck_cards, anchor_cards)
            deck_side = 0
        else:
            r = play_match(anchor_agent, deck_agent, anchor_cards, deck_cards)
            deck_side = 1
        if r.error:
            raise RuntimeError(f"{label} game {g}: {r.error}")
        if r.winner == 2:
            draws += 1
        elif r.winner == deck_side:
            wins += 1
        else:
            losses += 1
    return {"wins": wins, "draws": draws, "losses": losses, "games": n_games,
            "wr": wins / n_games if n_games else None}


def main(argv: list[str] | None = None) -> dict:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True,
                   help="Tournament DB path (opened READ-ONLY, mode=ro URI).")
    p.add_argument("--games", type=int, default=50,
                   help="games per sampled deck per run (default 50)")
    p.add_argument("--decks-per-stratum", type=int, default=10,
                   help="decks sampled per energy quartile (default 10; ~40 total)")
    p.add_argument("--runs", type=int, default=2,
                   help="independent replications (default 2; report BOTH)")
    p.add_argument("--baseline-games", type=int, default=200,
                   help="baseline-lineage games vs anchor per run (default 200)")
    p.add_argument("--seed", type=int, default=20260813)
    p.add_argument("--out-dir", default="experiments")
    args = p.parse_args(argv)

    conn = _connect_ro(Path(args.db))
    rows = conn.execute(_ACTIVE_DECKS_QUERY).fetchall()
    if not rows:
        raise SystemExit("no active concepts found — wrong DB?")

    comps = []          # (concept_id, deck_id, cards, energy, pokemon)
    unknown_skipped = 0
    for r in rows:
        cards = json.loads(r["cards"])
        try:
            en, pk = composition_counts(cards)
        except ValueError:
            unknown_skipped += 1
            continue
        comps.append((r["concept_id"], r["deck_id"], cards, en, pk))

    histograms = {
        "energy": dict(Counter(str(c[3]) for c in comps)),
        "pokemon": dict(Counter(str(c[4]) for c in comps)),
        "trainer": dict(Counter(str(60 - c[3] - c[4]) for c in comps)),
    }
    bounds = energy_quartile_bounds([c[3] for c in comps])

    strata: dict[int, list] = {0: [], 1: [], 2: [], 3: []}
    for c in comps:
        strata[stratum_of(c[3], bounds)].append(c)

    rng = random.Random(args.seed)
    sample = {
        q: rng.sample(members, min(args.decks_per_stratum, len(members)))
        for q, members in strata.items()
    }
    anchor_cards = _anchor_cards()

    runs = []
    for run_idx in range(args.runs):
        per_stratum = {}
        for q, members in sample.items():
            if not members:
                per_stratum[str(q)] = {"skipped": "empty stratum"}
                continue
            decks = []
            for concept_id_, deck_id_, cards, en, pk in members:
                series = play_series_vs_anchor(
                    cards, anchor_cards, args.games,
                    label=f"run{run_idx} q{q} {deck_id_}",
                )
                decks.append({"concept_id": concept_id_, "deck_id": deck_id_,
                              "energy": en, "pokemon": pk, **series})
            games = sum(d["games"] for d in decks)
            wins = sum(d["wins"] for d in decks)
            per_stratum[str(q)] = {
                "decks": decks,
                "pooled_wr": wins / games if games else None,
                "pooled_games": games,
            }
        runs.append({"run": run_idx, "strata": per_stratum})

    baseline_row = loop_state.current_baseline(conn)
    baseline: dict | None = None
    if baseline_row is not None:
        b_cards = json.loads(conn.execute(
            "SELECT cards FROM decks WHERE id=?", (baseline_row["deck_id"],)
        ).fetchone()["cards"])
        baseline = {
            "version": baseline_row["version"],
            "deck_id": baseline_row["deck_id"],
            "runs": [
                play_series_vs_anchor(
                    b_cards, anchor_cards, args.baseline_games,
                    label=f"baseline run{i}",
                )
                for i in range(args.runs)
            ],
        }
    else:
        print("WARNING: no current baseline found — baseline section omitted",
              file=sys.stderr)

    payload = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "db": str(args.db),
        "active_concepts": len(rows),
        "unknown_id_skipped": unknown_skipped,
        "energy_quartile_bounds": bounds,
        "histograms": histograms,
        "sample_seed": args.seed,
        "games_per_deck": args.games,
        "runs": runs,
        "baseline": baseline,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    json_path = out_dir / f"screening-regime-measurement-{stamp}.json"
    md_path = out_dir / f"screening-regime-measurement-{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md_lines = [
        f"# Screening-regime measurement — {stamp}",
        "",
        f"Active concepts: {len(rows)} (unknown-id skipped: {unknown_skipped})",
        f"Energy quartile bounds: {bounds}",
        "",
        "## Energy histogram",
        "",
        "| energy | decks |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in sorted(histograms["energy"].items(),
                                              key=lambda kv: int(kv[0]))],
        "",
        "## Stratified WR vs anchor (all runs reported)",
        "",
        "| run | stratum | pooled WR | games |", "|---|---|---|---|",
    ]
    for run in runs:
        for q, s in run["strata"].items():
            if "skipped" in s:
                md_lines.append(f"| {run['run']} | q{q} | skipped (empty) | 0 |")
            else:
                md_lines.append(
                    f"| {run['run']} | q{q} | {s['pooled_wr']:.3f} | {s['pooled_games']} |"
                )
    if baseline is not None:
        md_lines += ["", "## Baseline lineage vs anchor", ""]
        for i, b in enumerate(baseline["runs"]):
            md_lines.append(
                f"- run {i}: {b['wins']}/{b['games']} = {b['wr']:.3f} "
                f"({baseline['version']})"
            )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"wrote {json_path} and {md_path}")
    return payload


if __name__ == "__main__":
    main()
