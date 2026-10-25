"""Moves go to the belt tool over HTTP: lane 1 -> axis 0; busy waits; failures become move_failed."""
import asyncio

import httpx
from fastapi.testclient import TestClient

from server.adapters.bridge import HttpBeltBridge
from server.main import create_app
from tests.test_store import PLAYERS, recv_until, state_where


class FakeBelt:
    def __init__(self):
        self.calls, self.square, self.busy_times = [], {0: 0}, 0

    def transport(self):
        def handler(req: httpx.Request) -> httpx.Response:
            self.calls.append((req.method, req.url.path, dict(req.url.params)))
            axis = int(req.url.params.get("axis", 0))
            if req.url.path == "/board/goto":
                if self.busy_times:
                    self.busy_times -= 1
                    return httpx.Response(409, json={"detail": "A token is still moving"})
                if axis != 0:
                    return httpx.Response(503, json={"detail": "Motor move failed: no such axis"})
                self.square[axis] = int(req.url.params["square"])
                return httpx.Response(200, json={"from": 0, "to": {"square": self.square[axis]}, "moved": True})
            if req.url.path == "/board/set":
                self.square[axis] = int(req.url.params["square"])
                return httpx.Response(200, json={"square": self.square[axis]})
            return httpx.Response(404)
        return httpx.MockTransport(handler)


def test_lane_1_goes_to_axis_0_forward():
    belt = FakeBelt()
    b = HttpBeltBridge("http://belt:8001", lanes=1, transport=belt.transport())
    r = asyncio.run(b.move(1, 0, 6))
    assert r == {"type": "move_done", "lane": 1, "square": 6}
    assert belt.calls[-1] == ("POST", "/board/goto", {"square": "6", "direction": "forward", "axis": "0"})


def test_other_lanes_fall_back_to_the_stub():
    belt = FakeBelt()
    b = HttpBeltBridge("http://belt:8001", lanes=1, transport=belt.transport())
    r = asyncio.run(b.move(2, 0, 5))
    assert r["type"] == "move_done" and r["square"] == 5 and not belt.calls


def test_busy_then_ok_and_failure():
    belt = FakeBelt(); belt.busy_times = 2
    b = HttpBeltBridge("http://belt:8001", lanes=2, transport=belt.transport(), busy_wait=5)
    assert asyncio.run(b.move(1, 0, 3))["type"] == "move_done" and len(belt.calls) == 3
    r = asyncio.run(b.move(2, 0, 3))                       # axis 1 fails on the fake belt
    assert r["type"] == "move_failed" and "no such axis" in r["reason"]


def test_unreachable_is_move_failed():
    def boom(req):
        raise httpx.ConnectError("refused")
    b = HttpBeltBridge("http://belt:8001", transport=httpx.MockTransport(boom))
    assert asyncio.run(b.move(1, 0, 3))["type"] == "move_failed"


def test_game_start_resets_and_a_table_roll_moves_the_physical_token():
    belt = FakeBelt(); belt.square[0] = 12                 # stale tracked square from bench testing
    with TestClient(create_app(sim=True, move_delay=0,
                               bridge_factory=lambda: HttpBeltBridge("http://belt:8001", lanes=1, transport=belt.transport()))) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            recv_until(board, lambda m: m["type"] == "state")
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            c.post("/vision/events", json={"type": "dice_rolled", "values": [3, 3], "total": 6})
            s = state_where(board, lambda s: s["players"][0]["position"] == 6 and s["phase"] != "MOVING")
        assert ("POST", "/board/set", {"square": "0", "axis": "0"}) in belt.calls     # reset at game start
        assert belt.calls[-1] == ("POST", "/board/goto", {"square": "6", "direction": "forward", "axis": "0"})
        assert belt.square[0] == 6
