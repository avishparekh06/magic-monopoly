"""Move bridge: turns engine move commands into physical token movement.

in:  move(lane=2, frm=3, to=8)
out: {"type": "move_done", "lane": 2, "square": 8}
   | {"type": "move_failed", "lane": 2, "reason": "timeout"}

Moves always go forward and wrap past 39. The engine does not resolve
the square until it sees move_done.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from ..rules.monopoly import steps_between

log = logging.getLogger("bridge")


class MoveBridge(ABC):
    @abstractmethod
    async def move(self, lane: int, frm: int, to: int) -> dict:
        """Move the token in `lane` forward from `frm` to `to`; return when finished."""


class StubBridge(MoveBridge):
    def __init__(self, delay: float = 1.0):
        self.delay = delay

    async def move(self, lane: int, frm: int, to: int) -> dict:
        log.info("moved lane %d from %d to %d (%d squares)", lane, frm, to, steps_between(frm, to))
        await asyncio.sleep(self.delay)
        return {"type": "move_done", "lane": lane, "square": to}


class GrblBridge(MoveBridge):
    """Real implementation: G-code over serial to an Arduino running GRBL."""

    def __init__(self, port: str = "/dev/ttyUSB0"):
        raise NotImplementedError("GRBL bridge not built yet; run with --sim")

    async def move(self, lane: int, frm: int, to: int) -> dict:
        raise NotImplementedError
