"""Agent protocol. An agent maps an engine Observation to selected option indices."""
from __future__ import annotations

from abc import ABC, abstractmethod

from cg.api import Observation


class Agent(ABC):
    name: str = "agent"

    @abstractmethod
    def act(self, obs: Observation) -> list[int]:
        """Return option indices: minCount <= len <= maxCount, unique, in range."""
