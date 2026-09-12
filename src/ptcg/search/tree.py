"""MCTS tree keyed by canonical action signatures.

Option indices are NOT stable across determinizations, and serials of predicted
hidden cards differ per determinization. Signatures therefore use only
content-bearing fields. None -> -1 keeps tuples hashable and sortable.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Sequence

from cg.api import Option, SelectData

ActionSig = tuple


def _n(x) -> int:
    return -1 if x is None else int(x)


def option_signature(opt: Option) -> tuple:
    return (_n(opt.type), _n(opt.cardId), _n(opt.attackId), _n(opt.number),
            _n(opt.area), _n(opt.playerIndex), _n(opt.toolIndex), _n(opt.energyIndex),
            _n(opt.count), _n(opt.inPlayArea), _n(opt.inPlayIndex),
            _n(opt.specialConditionType))


def action_signature(select: SelectData, indices: Sequence[int]) -> ActionSig:
    return tuple(sorted(option_signature(select.option[i]) for i in indices))


@dataclass
class Node:
    visits: int = 0
    value_sum: float = 0.0
    children: dict[ActionSig, "Node"] = field(default_factory=dict)
    priors: dict | None = None  # policy-net PUCT priors, cached at expansion (7B)

    @property
    def mean(self) -> float:
        return self.value_sum / self.visits if self.visits else 0.0


def ucb_pick(node: Node, legal: Sequence[ActionSig], c: float,
             rng: random.Random) -> ActionSig:
    unvisited = [s for s in legal
                 if s not in node.children or node.children[s].visits == 0]
    if unvisited:
        return rng.choice(unvisited)
    log_n = math.log(max(node.visits, 1))
    return max(legal, key=lambda s: (node.children[s].mean
                                     + c * math.sqrt(log_n / node.children[s].visits)))


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
