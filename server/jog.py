"""Hand-jogging a belt motor before a demo: run until Stop, nudge a few steps, call the spot a square.

The belt tool (arduino/server.py, usually `uvicorn server:app --port 8001`) owns the Arduino's serial port, so the
game server can't drive the motor itself. These routes pass the buttons on to the belt tool's /jog, /stop and
/board/set. The long "run until Stop" is repeated /jog calls that end when /jog/stop is pressed.
"""
from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, HTTPException, Request

RUN_STEPS = 20_000    # the belt tool's /jog limit: about a minute at the plain step period, then it goes again


def jog_router(is_local, bench_url: str, axes: dict[int, int], transport=None) -> APIRouter:
    """`axes` maps a player's lane to the motor axis on the belt tool; every button takes ?player=<lane>."""
    router = APIRouter(prefix="/jog")
    state: dict = {"task": None}

    def local_only(request: Request) -> None:
        if not is_local(request):
            raise HTTPException(403, "motor jogging is only for the board laptop")

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=bench_url.rstrip("/"), timeout=120, transport=transport)

    def axis_of(player: int | None) -> int:
        player = min(axes) if player is None else player
        if player not in axes:
            raise HTTPException(422, f"player must be one of {sorted(axes)}")
        return axes[player]

    async def call(path: str, axis: int, **params) -> dict:
        try:
            async with client() as c:
                r = await c.post(path, params={**params, "axis": axis})
        except httpx.HTTPError as e:
            raise HTTPException(503, f"belt tool unreachable: {e}")
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise HTTPException(r.status_code, str(detail)[:160])
        return r.json()

    async def run(sign: int, axis: int) -> None:
        try:
            while True:
                await call("/jog", axis, steps=sign * RUN_STEPS)
        except HTTPException:
            pass          # Stop was pressed (the belt tool answers the interrupted move) or the motor failed

    @router.post("/start")
    async def start(request: Request, direction: str = "forward", player: int | None = None):
        """Run the motor until /jog/stop."""
        local_only(request)
        axis = axis_of(player)
        if direction not in ("forward", "back"):
            raise HTTPException(422, "direction must be forward or back")
        if state["task"] and not state["task"].done():
            raise HTTPException(409, "The motor is already running")
        state["task"] = asyncio.create_task(run(1 if direction == "forward" else -1, axis))
        await asyncio.sleep(0.3)             # let a refusal (token busy, belt tool down) show up as the answer
        if state["task"].done():
            raise HTTPException(409, "The motor would not start: is the token moving or the belt tool down?")
        return {"running": True, "player": player, "axis": axis, "direction": direction}

    @router.post("/stop")
    async def stop(request: Request, player: int | None = None):
        local_only(request)
        await call("/stop", axis_of(player))
        task = state["task"]
        if task:
            await asyncio.wait_for(task, 5)
        return {"running": False}

    @router.post("/nudge")
    async def nudge(request: Request, steps: int = 50, player: int | None = None):
        """Move by exactly `steps` motor steps (negative = back)."""
        local_only(request)
        if not 0 < abs(steps) <= 5000:
            raise HTTPException(422, "nudge is for small moves: 1 to 5000 steps")
        return await call("/jog", axis_of(player), steps=steps)

    @router.post("/home")
    async def home(request: Request, square: int = 0, player: int | None = None):
        """Tell the belt tool which square the token is on now (no motor move)."""
        local_only(request)
        return await call("/board/set", axis_of(player), square=square)

    return router
