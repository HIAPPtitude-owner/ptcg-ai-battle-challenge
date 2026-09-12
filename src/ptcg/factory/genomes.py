"""Genome models + pool ledgers for the evolutionary agent population.

An AgentGenome captures the evolvable search-agent hyperparameters (see
GENE_SPEC / CATEGORICAL_GENES below) plus lineage/rating bookkeeping; a
DeckGenome captures an evolvable 60-card deck list plus the same
bookkeeping. Both reuse the exact JSON-ledger conventions candidates.py
already established for Candidate (atomic tmp-then-os.replace, utf-8,
back-compat schema tolerance, refresh-under-lock merge), so later
co-evolution tasks (breeding, a two-factor rating fit, a tournament tick)
can treat agent_pool.json / deck_pool.json exactly like candidates.json.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

from ptcg.factory.candidates import ledger_lock
from ptcg.factory.deck_matrix import deck_hash

# Numeric gene bounds: name -> (lo, hi, mutation_sigma, cast). These are the
# ONLY evolvable numeric keys in an AgentGenome.config; consumed by
# build_agent (a later task) to construct a real SearchConfig.
GENE_SPEC: dict[str, tuple] = {
    "search_budget_ms":       (50,   1000, 60,   int),
    "rollout_depth":          (0,    24,   3,    int),
    "deviate_min_visits":     (0,    60,   6,    int),
    "deviate_value_edge":     (0.0,  0.4,  0.04, float),
    "c_uct":                  (0.5,  3.0,  0.25, float),
    "c_puct":                 (0.5,  3.0,  0.25, float),
    "prior_tau":              (0.02, 1.0,  0.1,  float),
    "max_depth":              (20,   80,   6,    int),
    "robust_min_visits":      (1,    20,   2,    int),
    "deviate_min_visit_frac": (0.0,  0.5,  0.05, float),
}

# Categorical gene choices: name -> tuple of allowed values. Also evolvable,
# but mutated by resampling from the tuple rather than a Gaussian sigma.
CATEGORICAL_GENES: dict[str, tuple] = {
    "final_move_rule": ("most_visited", "max_value"),
    "use_root_prior":  (False, True),
}


# statuses: "live" | "retired" | "anchor" | "meta-anchor" (meta-anchor is deck-only)
@dataclass
class AgentGenome:
    id: str                    # "ag-" + sha1(canonical config json)[:10]
    kind: str                  # "search" | "heuristic" (heuristic only for anchors)
    config: dict               # full gene dict (see GENE_SPEC keys)
    status: str = "live"
    lineage: list = field(default_factory=list)   # parent genome ids
    born_at: str = ""          # ISO UTC
    seed: int = 0              # RNG seed used at this genome's birth
    rating: float | None = None   # two-factor log-strength (additive scale)
    games: int = 0
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, row: dict) -> "AgentGenome":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in row.items() if k in known})


@dataclass
class DeckGenome:
    id: str                    # "dk-" + deck_hash(cards)[:10]
    cards: list = field(default_factory=list)      # 60 card ids
    csv: str = ""              # repo-relative path of the written deck csv
    net_weights: str | None = None  # repo-relative value-net weights path
    status: str = "live"
    lineage: list = field(default_factory=list)
    born_at: str = ""
    seed: int = 0
    rating: float | None = None
    games: int = 0
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, row: dict) -> "DeckGenome":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in row.items() if k in known})


def agent_genome_id(config: dict) -> str:
    """Content-addressed id: order-insensitive over dict key insertion
    (json.dumps sort_keys=True), so re-deriving the same gene values always
    yields the same id regardless of how the config dict was built."""
    canonical = json.dumps(config, sort_keys=True)
    return "ag-" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:10]


def deck_genome_id(cards: list) -> str:
    return "dk-" + deck_hash(cards)[:10]


def cell_id(agent_id: str, deck_id: str) -> str:
    return f"cell~{agent_id}~{deck_id}"


def split_cell_id(cid: str) -> tuple[str, str]:
    parts = cid.split("~")
    if len(parts) != 3 or parts[0] != "cell":
        raise ValueError(f"malformed cell id: {cid!r}")
    return parts[1], parts[2]


def _genome_from_dict(row: dict):
    """id prefix ("ag-"/"dk-") discriminates genome type on load - no
    existing candidate/genome id convention in this repo contains "~" or
    collides across the two prefixes (verified against make_id at
    candidates.py:41)."""
    gid = row.get("id", "")
    if gid.startswith("ag-"):
        return AgentGenome.from_dict(row)
    if gid.startswith("dk-"):
        return DeckGenome.from_dict(row)
    raise ValueError(f"cannot determine genome type for id {gid!r}")


def load_pool(path: Path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [_genome_from_dict(row) for row in payload.get("genomes", [])]


def save_pool(path: Path, genomes: list) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)  # virgin-dir rule
    payload = {"version": 1, "genomes": [g.to_dict() for g in genomes]}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def pool_merge_save(path: Path, modified: list,
                    log: Callable[[str], None] | None = None) -> list:
    """Refresh-under-lock merge, same semantics as candidates.merge_save
    (candidates.py:189): under a single `ledger_lock` hold, fresh-loads the
    pool from disk, replaces entries whose ids appear in `modified`
    (preserving every disk-only entry `modified` never touched), appends any
    ids in `modified` not yet present on disk, then saves the merged result
    and returns it. Not reentrant - callers must not already be holding
    `path`'s lock.
    """
    path = Path(path)
    with ledger_lock(path, log=log):
        fresh = load_pool(path)
        fresh_ids = {g.id for g in fresh}
        by_id = {g.id: g for g in modified}
        merged = [by_id.get(g.id, g) for g in fresh]
        merged.extend(g for g in modified if g.id not in fresh_ids)
        save_pool(path, merged)
    return merged
