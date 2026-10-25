"""Hand-jogging the belt before a demo: run the motor until Stop, nudge it a few steps, call the spot GO.

These drive the motor server's own board (arduino/server.py, loaded by server/hardware.py), so they share its
lock: a jog and a game move can never run at once.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

RUN_STEPS = 200_000   # a "run until Stop" move: ~10 minutes at the plain step period, cut short by /jog/stop


def jog_router(is_local) -> APIRouter:
    from .hardware import motor_server
    hw = motor_server()
    router = APIRouter(prefix="/jog")
    state = {"running": False}

    def local_only(request: Request) -> None:
        if not is_local(request):
            raise HTTPException(403, "motor jogging is only for the board laptop")

    def sign(direction: str) -> int:
        if direction not in ("forward", "back"):
            raise HTTPException(422, "direction must be forward or back")
        return 1 if direction == "forward" else -1

    @router.post("/start")
    def start(request: Request, direction: str = "forward", axis: int | None = None):
        """Run the motor until /jog/stop."""
        local_only(request)
        axis = hw.pick_axis(axis)
        steps = sign(direction) * RUN_STEPS
        if not hw.lock.acquire(blocking=False):
            raise HTTPException(409, "The token is already moving")
        try:
            hw.stop_requested.clear()
            if not hw.DRY_RUN:
                hw.get_board().move(axis, steps, hw.PERIODS[axis][0], wait=False)
        except Exception as e:
            hw.lock.release()
            raise HTTPException(503, f"Motor move failed: {e}")
        state["running"] = True
        return {"running": True, "axis": axis, "direction": direction}

    @router.post("/stop")
    def stop(request: Request):
        local_only(request)
        if not hw.DRY_RUN:
            try:
                hw.get_board().stop()
            except Exception as e:
                raise HTTPException(503, f"Stop failed: {e}")
        if state["running"]:
            state["running"] = False
            hw.lock.release()
        return {"running": False}

    @router.post("/nudge")
    def nudge(request: Request, steps: int = 50, axis: int | None = None):
        """Move by exactly `steps` motor steps (negative = back)."""
        local_only(request)
        axis = hw.pick_axis(axis)
        if abs(steps) > 5000:
            raise HTTPException(422, "nudge is for small moves: at most 5000 steps")
        if not hw.lock.acquire(blocking=False):
            raise HTTPException(409, "The token is moving: press Stop first")
        try:
            hw.drive(axis, steps, hw.PERIODS[axis][0])
            return {"axis": axis, "steps": steps}
        finally:
            hw.lock.release()

    @router.post("/home")
    def home(request: Request, axis: int | None = None):
        """Call the token's spot GO (square 0) without moving the motor."""
        local_only(request)
        axis = hw.pick_axis(axis)
        hw.put_square(axis, 0)
        return {"axis": axis, "square": 0}

    return router
