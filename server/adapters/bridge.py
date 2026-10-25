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

    async def reset(self, lanes: list[int]) -> None:
        """A game just started with every token on the start square. Default: nothing to do."""

    async def restore(self, positions: dict[int, int]) -> None:
        """A saved game was resumed: {lane: square} is where each token belongs.

        Positions are dead-reckoned, so after a restart nobody knows where the
        carriages physically are. Real hardware must home every lane to Castle
        Gate first (switch, camera mark, or the operator placing tokens by hand
        and zeroing), then drive each lane forward to its square."""
        log.info("resume: re-zero tokens at Once Upon a Time, then drive lanes to %s", positions)


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


class HttpBeltBridge(MoveBridge):
    """Drives tokens through the belt tool's HTTP API (arduino/server.py running anywhere:
    this laptop on another port, or the teammate's machine that has the Arduino).

    Lane N is axis N-1. POST /board/goto?square=<to>&direction=forward&axis=<lane-1> blocks
    until the motor has finished and answers {"to": {"square": ...}, "moved": ...}.
    409 means someone is jogging that token by hand: wait and retry. Anything else is move_failed.
    Lanes beyond `lanes` (only track 1 is built today) go to the fallback (stub) bridge.
    """

    def __init__(self, base_url: str, lanes: int = 1, fallback: MoveBridge | None = None,
                 busy_wait: float = 60.0, move_timeout: float = 120.0, transport=None):
        self.base_url = base_url.rstrip("/")
        self.lanes = lanes
        self.fallback = fallback or StubBridge(0.5)
        self.busy_wait = busy_wait
        self.move_timeout = move_timeout
        self._transport = transport

    def _client(self):
        import httpx
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.move_timeout, transport=self._transport)

    async def move(self, lane: int, frm: int, to: int) -> dict:
        if lane > self.lanes:
            return await self.fallback.move(lane, frm, to)
        axis = lane - 1
        waited = 0.0
        async with self._client() as c:
            while True:
                try:
                    r = await c.post("/board/goto", params={"square": to, "direction": "forward", "axis": axis})
                except Exception as e:
                    return {"type": "move_failed", "lane": lane, "reason": f"belt server unreachable: {e}"}
                if r.status_code == 409 and waited < self.busy_wait:
                    await asyncio.sleep(0.3)
                    waited += 0.3
                    continue
                if r.status_code != 200:
                    detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
                    return {"type": "move_failed", "lane": lane, "reason": str(detail)[:160]}
                square = r.json().get("to", {}).get("square", to)
                log.info("belt lane %d: %d -> %d (axis %d)", lane, frm, square, axis)
                return {"type": "move_done", "lane": lane, "square": square}

    async def reset(self, lanes: list[int]) -> None:
        """A game just started: every token was placed on Once Upon a Time by hand, so tell the belt
        tool each tracked square is 0 (no motor move)."""
        async with self._client() as c:
            for lane in lanes:
                if lane <= self.lanes:
                    try:
                        await c.post("/board/set", params={"square": 0, "axis": lane - 1})
                    except Exception as e:
                        log.warning("belt lane %d: could not reset tracked square: %s", lane, e)

    async def restore(self, positions: dict[int, int]) -> None:
        """Resumed game: align the belt tool's tracked squares with the game, so the next move goes
        the right distance, assuming the operator put the tokens back where the board page shows them."""
        log.info("resume: put tokens at %s (lane: square), then the belt tool is told those squares", positions)
        async with self._client() as c:
            for lane, square in positions.items():
                if lane <= self.lanes:
                    try:
                        await c.post("/board/set", params={"square": square, "axis": lane - 1})
                    except Exception as e:
                        log.warning("belt lane %d: could not set tracked square: %s", lane, e)


class StepBeltBridge(MoveBridge):
    """Drives track 1 through a minimal remote belt service:

        POST <base>/step?squares=<n>&key=<key>      move the token n squares forward

    Moves are relative, so the bridge sends the forward distance between the engine's from and
    to squares. There is no way to set or read the tracked square, so the token must start on
    Once Upon a Time; a drift is fixed by jogging the token by hand. Lanes above `lanes` are simulated.
    """

    def __init__(self, base_url: str, key: str, lanes: int = 1, fallback: MoveBridge | None = None,
                 move_timeout: float = 120.0, transport=None):
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.lanes = lanes
        self.fallback = fallback or StubBridge(0.5)
        self.move_timeout = move_timeout
        self._transport = transport

    async def move(self, lane: int, frm: int, to: int) -> dict:
        if lane > self.lanes:
            return await self.fallback.move(lane, frm, to)
        n = steps_between(frm, to)
        if n == 0:
            return {"type": "move_done", "lane": lane, "square": to}
        import httpx
        try:
            async with httpx.AsyncClient(timeout=self.move_timeout, transport=self._transport) as c:
                r = await c.post(f"{self.base_url}/step", params={"squares": n, "key": self.key})
        except Exception as e:
            return {"type": "move_failed", "lane": lane, "reason": f"belt service unreachable: {e}"}
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            return {"type": "move_failed", "lane": lane, "reason": f"belt service {r.status_code}: {str(detail)[:140]}"}
        log.info("belt lane %d: stepped %d squares (%d -> %d)", lane, n, frm, to)
        return {"type": "move_done", "lane": lane, "square": to}

    async def reset(self, lanes: list[int]) -> None:
        log.info("game start: token on lane 1 must be on Once Upon a Time (the step service has no set-position)")

    async def restore(self, positions: dict[int, int]) -> None:
        log.info("resume: place tokens by hand at %s (lane: square); moves continue relative from there", positions)
