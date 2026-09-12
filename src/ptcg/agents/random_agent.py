"""Uniform-random legal agent — the floor any real agent must dominate."""
from __future__ import annotations

import random

from cg.api import Observation

from .base import Agent


class RandomAgent(Agent):
    name = "random"

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)

    def act(self, obs: Observation) -> list[int]:
        sel = obs.select
        k = self.rng.randint(sel.minCount, sel.maxCount)
        return self.rng.sample(range(len(sel.option)), k)
