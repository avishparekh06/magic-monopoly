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

from ..rules.monopoly import NUM_SQUARES, steps_between

log = logging.getLogger("bridge")


class MoveBridge(ABC):
    @abstractmethod
    async def move(self, lane: int, frm: int, to: int) -> dict:
        """Move the token in `lane` forward from `frm` to `to`; return when finished."""

    async def reset(self, lanes: list[int]) -> None:
        """A game just started with every token on the start square. Default: nothing to do."""

    async def positions(self) -> dict[int, int]:
        """{lane: square} for every lane whose physical position can be read. Default: none."""
        return {}

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

    His server drives one token (axis 0), so lane 1 (the host's token) goes through his
    board_goto(); other lanes use `fallback` until his server supports them.
    Set DRY_RUN=1 to run his code without an Arduino attached.
    """

    def __init__(self, fallback: MoveBridge, busy_wait: float = 60.0):
        from ..hardware import motor_server
        self.hw = motor_server()
        self.fallback = fallback
        self.busy_wait = busy_wait

    async def reset(self, lanes: list[int]) -> None:
        if 1 in lanes:
            self.hw.put_square(self.hw.MOTOR_AXIS, 0)

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

    Lane N is axis N-1. POST /board/goto?square=<to>&direction=auto&axis=<lane-1> (the shorter way) blocks
    until the motor has finished and answers {"to": {"square": ...}, "moved": ...}.
    409 means someone is jogging that token by hand: wait and retry. Anything else is move_failed.
    Lanes beyond `lanes` (only track 1 is built today) go to the fallback (stub) bridge.
    `axes` maps a lane to a chosen motor axis instead ({2: 0}: lane 2 drives axis 0), and that lane is real
    whatever `lanes` says.
    """

    def __init__(self, base_url: str, lanes: int = 1, fallback: MoveBridge | None = None,
                 busy_wait: float = 60.0, move_timeout: float = 120.0, transport=None,
                 axes: dict[int, int] | None = None):
        self.base_url = base_url.rstrip("/")
        self.lanes = lanes
        self.axes = axes or {}
        self.fallback = fallback or StubBridge(0.5)
        self.busy_wait = busy_wait
        self.move_timeout = move_timeout
        self._transport = transport

    def _client(self):
        import httpx
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.move_timeout, transport=self._transport)

    def _real(self, lane: int) -> bool:
        return lane in self.axes or lane <= self.lanes

    def _axis(self, lane: int) -> int:
        return self.axes.get(lane, lane - 1)

    async def move(self, lane: int, frm: int, to: int) -> dict:
        if not self._real(lane):
            return await self.fallback.move(lane, frm, to)
        axis = self._axis(lane)
        waited = 0.0
        async with self._client() as c:
            while True:
                try:
                    r = await c.post("/board/goto", params={"square": to, "direction": "auto", "axis": axis})
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

    async def positions(self) -> dict[int, int]:
        """The belt tool's tracked square for every real lane (GET /board?axis=)."""
        out = {}
        try:
            async with self._client() as c:
                for lane in sorted(set(self.axes) | set(range(1, self.lanes + 1))):
                    r = await c.get("/board", params={"axis": self._axis(lane)})
                    if r.status_code == 200 and r.json().get("square") is not None:
                        out[lane] = int(r.json()["square"])
        except Exception as e:
            log.warning("belt tool positions unreachable: %s", e)
        return out

    async def reset(self, lanes: list[int]) -> None:
        """A game just started: every token was placed on Once Upon a Time by hand, so tell the belt
        tool each tracked square is 0 (no motor move)."""
        async with self._client() as c:
            for lane in lanes:
                if self._real(lane):
                    try:
                        await c.post("/board/set", params={"square": 0, "axis": self._axis(lane)})
                    except Exception as e:
                        log.warning("belt lane %d: could not reset tracked square: %s", lane, e)

    async def restore(self, positions: dict[int, int]) -> None:
        """Resumed game: align the belt tool's tracked squares with the game, so the next move goes
        the right distance, assuming the operator put the tokens back where the board page shows them."""
        log.info("resume: put tokens at %s (lane: square), then the belt tool is told those squares", positions)
        async with self._client() as c:
            for lane, square in positions.items():
                if self._real(lane):
                    try:
                        await c.post("/board/set", params={"square": square, "axis": self._axis(lane)})
                    except Exception as e:
                        log.warning("belt lane %d: could not set tracked square: %s", lane, e)


class StepBeltBridge(MoveBridge):
    """Drives track 1 through the remote belt gateway (behind a Cloudflare tunnel):

        GET  <base>/position?key=<key>               -> {"square": 14, "name": ..., "type": ...}
        POST <base>/step?squares=<n>&key=<key>       -> {"ok": true, "square": 17, "from": {"id": 14, ...},
                                                         "squares": 3, "motor_steps": ..., "moved": true, "passed_go": ...}

    Square ids are the game's own (0-39). Before each move the bridge asks where the token really is
    and steps the forward distance from there to the engine's target, so a stale or drifted tracked
    square self-corrects instead of compounding. The square the gateway reports afterwards must be
    the target, or the move is reported as failed. Lanes above `lanes` are simulated.
    """

    def __init__(self, base_url: str, key: str, lanes: int = 1, fallback: MoveBridge | None = None,
                 move_timeout: float = 120.0, transport=None):
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.lanes = lanes
        self.fallback = fallback or StubBridge(0.5)
        self.move_timeout = move_timeout
        self._transport = transport

    def _client(self):
        import httpx
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.move_timeout, transport=self._transport)

    async def position(self) -> int | None:
        """The gateway's tracked square for track 1, or None if it cannot be read."""
        try:
            async with self._client() as c:
                r = await c.get("/position", params={"key": self.key})
            if r.status_code == 200:
                sq = r.json().get("square")
                return int(sq) if sq is not None else None
            log.warning("belt /position -> %s %s", r.status_code, r.text[:120])
        except Exception as e:
            log.warning("belt /position unreachable: %s", e)
        return None

    async def positions(self) -> dict[int, int]:
        """Track 1 from the gateway, plus whatever lanes the fallback can read."""
        out = {}
        pos = await self.position()
        if pos is not None:
            out[1] = pos
        out.update(await self.fallback.positions())
        return out

    async def move(self, lane: int, frm: int, to: int) -> dict:
        if lane > self.lanes:
            return await self.fallback.move(lane, frm, to)
        actual = await self.position()
        start = actual if actual is not None else frm
        if actual is not None and actual != frm:
            log.warning("belt lane %d: gateway says square %d, game expected %d; stepping from %d", lane, actual, frm, actual)
        n = steps_between(start, to)
        if n > NUM_SQUARES // 2:                 # the shorter way round: back, not most of the way forward
            n -= NUM_SQUARES
        if n == 0:
            return {"type": "move_done", "lane": lane, "square": to}
        try:
            async with self._client() as c:
                r = await c.post("/step", params={"squares": n, "key": self.key})
        except Exception as e:
            return {"type": "move_failed", "lane": lane, "reason": f"belt service unreachable: {e}"}
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            return {"type": "move_failed", "lane": lane, "reason": f"belt service {r.status_code}: {str(detail)[:140]}"}
        try:
            body = r.json()
        except ValueError:
            body = {}
        reported = body.get("square")
        if isinstance(reported, int) and reported != to:
            return {"type": "move_failed", "lane": lane,
                    "reason": f"belt stepped {n} but reports square {reported}, expected {to}; check Set position"}
        log.info("belt lane %d: stepped %+d squares (%d -> %d, motor_steps=%s)", lane, n, start, to, body.get("motor_steps"))
        return {"type": "move_done", "lane": lane, "square": to}

    async def reset(self, lanes: list[int]) -> None:
        """Game start: the game puts every token on square 0. The gateway has no set-position, so if its
        tracked square differs, the first move simply steps from wherever it says the token is."""
        pos = await self.position()
        if pos is None:
            log.warning("game start: belt gateway position unknown; first move steps relative to square 0")
        elif pos != 0:
            log.warning("game start: belt gateway says track 1 is on square %d, game starts at 0; "
                        "the first move will drive it from %d (use the bench tool's Set position if that is wrong)", pos, pos)
        else:
            log.info("game start: belt track 1 at square 0")

    async def restore(self, positions: dict[int, int]) -> None:
        pos = await self.position()
        log.info("resume: game has tokens at %s (lane: square); belt gateway says track 1 is at %s", positions, pos)
