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

    async def restore(self, positions: dict[int, int]) -> None:
        """A saved game was resumed: {lane: square} is where each token belongs.

        Positions are dead-reckoned, so after a restart nobody knows where the
        carriages physically are. Real hardware must home every lane to Castle
        Gate first (switch, camera mark, or the operator placing tokens by hand
        and zeroing), then drive each lane forward to its square."""
        log.info("resume: re-zero tokens at Castle Gate, then drive lanes to %s", positions)


class StubBridge(MoveBridge):
    def __init__(self, delay: float = 1.0):
        self.delay = delay

    async def move(self, lane: int, frm: int, to: int) -> dict:
        log.info("moved lane %d from %d to %d (%d squares)", lane, frm, to, steps_between(frm, to))
        await asyncio.sleep(self.delay)
        return {"type": "move_done", "lane": lane, "square": to}


class ArduinoBridge(MoveBridge):
    """Moves through Avish's motor code (arduino/server.py), called in-process.

    His server drives one token (axis 0), so lane 1 (Grandma) goes through his
    board_goto(); other lanes use `fallback` until his server supports them.
    Set DRY_RUN=1 to run his code without an Arduino attached.
    """

    def __init__(self, fallback: MoveBridge, busy_wait: float = 60.0):
        from ..hardware import motor_server
        self.hw = motor_server()
        self.fallback = fallback
        self.busy_wait = busy_wait

    async def move(self, lane: int, frm: int, to: int) -> dict:
        if lane != 1:
            return await self.fallback.move(lane, frm, to)
        from fastapi import HTTPException
        waited = 0.0
        while True:
            try:
                # His endpoint function blocks until the motor is done, so run it off the event loop.
                # It goes forward from wherever his tracked square is, so a /board/set re-home is respected.
                result = await asyncio.to_thread(self.hw.board_goto, to, "forward")
            except HTTPException as e:
                if e.status_code == 409 and waited < self.busy_wait:  # someone is jogging the token
                    await asyncio.sleep(0.3)
                    waited += 0.3
                    continue
                return {"type": "move_failed", "lane": lane, "reason": str(e.detail)}
            except Exception as e:
                return {"type": "move_failed", "lane": lane, "reason": str(e)}
            return {"type": "move_done", "lane": lane, "square": result["to"]["square"]}
