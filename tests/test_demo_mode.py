"""Demo mode: jog Player 2's belt through the belt tool, then Resume (demo) plays the scripted moves."""
import asyncio
import time

import httpx
from fastapi.testclient import TestClient

from server.adapters.bridge import HttpBeltBridge, MoveBridge, StubBridge
from server.demo import DEMO_MOVES, DEMO_PLAYERS
from server.main import create_app


class FakeBench:
    """The belt tool (arduino/server.py): records every call and tracks the saved square per axis."""

    def __init__(self):
        self.calls = []
        self.squares = {0: 0}
        self.busy = False

    async def handler(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        self.calls.append((request.url.path, q))
        if request.url.path == "/jog":
            if self.busy:
                return httpx.Response(409, json={"detail": "A token is still moving"})
            await asyncio.sleep(0.05)                           # a real /jog blocks while the motor turns
        if request.url.path == "/board/set":
            self.squares[int(q["axis"])] = int(q["square"])
        if request.url.path == "/board/goto":
            self.squares[int(q["axis"])] = int(q["square"])
            return httpx.Response(200, json={"to": {"square": int(q["square"])}, "moved": True})
        return httpx.Response(200, json={"ok": True})

    def transport(self):
        return httpx.MockTransport(self.handler)


def app_client(bench: FakeBench, **kw):
    return TestClient(create_app(
        sim=True, move_delay=0, dev=True, virtual_axis=0,
        bridge_factory=lambda: HttpBeltBridge("http://bench", lanes=0, axes={2: 0}, fallback=StubBridge(0),
                                              transport=bench.transport()), **kw))


def test_jog_routes_need_dev_and_a_virtual_axis():
    with TestClient(create_app(sim=True, move_delay=0, virtual_axis=0)) as c:
        assert c.post("/jog/stop").status_code == 404
        assert c.get("/api/config").json()["jog"] is False
    with TestClient(create_app(sim=True, move_delay=0, dev=True)) as c:
        assert c.get("/api/config").json()["jog"] is False


def test_nudge_and_home_go_to_the_belt_tool(monkeypatch):
    bench = FakeBench()
    monkeypatch.setenv("BENCH_URL", "http://bench")
    import server.jog as jog
    real = jog.httpx.AsyncClient
    monkeypatch.setattr(jog.httpx, "AsyncClient",
                        lambda **kw: real(**{**kw, "transport": bench.transport()}))
    with app_client(bench) as c:
        assert c.get("/api/config").json()["jog"] is True
        assert c.post("/jog/nudge", params={"steps": 50}).status_code == 200
        assert c.post("/jog/nudge", params={"steps": -50}).status_code == 200
        assert c.post("/jog/nudge", params={"steps": 99999}).status_code == 422
        assert c.post("/jog/home", params={"square": 1}).status_code == 200
        assert c.post("/jog/start", params={"direction": "sideways"}).status_code == 422
        assert [(p, q.get("steps"), q["axis"]) for p, q in bench.calls[:2]] == [("/jog", "50", "0"), ("/jog", "-50", "0")]
        assert bench.squares[0] == 1


def test_start_keeps_jogging_until_stop(monkeypatch):
    bench = FakeBench()
    import server.jog as jog
    real = jog.httpx.AsyncClient
    monkeypatch.setattr(jog.httpx, "AsyncClient",
                        lambda **kw: real(**{**kw, "transport": bench.transport()}))
    with app_client(bench) as c:
        assert c.post("/jog/start", params={"direction": "back"}).json()["running"] is True
        time.sleep(0.2)
        assert any(p == "/jog" and q["steps"] == "-20000" for p, q in bench.calls)
        bench.busy = True                                       # the belt tool refuses further /jog: the run ends
        assert c.post("/jog/stop").json() == {"running": False}
        assert ("/stop", {"axis": "0"}) in bench.calls


def test_demo_resume_plays_the_scripted_moves_on_the_belt():
    assert [(p["name"], p["position"]) for p in DEMO_PLAYERS] == [("Player 1", 3), ("Player 2", 1)]
    assert DEMO_MOVES == [("p2", 3), ("p1", 1), ("p2", 3)]
    bench = FakeBench()
    with app_client(bench) as c:
        assert c.post("/api/demo/resume").status_code == 200
        gid = next(iter(c.app.state.rooms))
        room = c.app.state.rooms[gid]
        for _ in range(50):
            if [p.position for p in room.state.players] == [4, 7]:
                break
            time.sleep(0.1)
        assert [p.position for p in room.state.players] == [4, 7]
        assert room.state.phase.value == "WAITING_FOR_ROLL"
        # Player 2's belt (axis 0) was told it starts on Black Forest, then driven to square 4
        assert ("/board/set", {"square": "1", "axis": "0"}) in bench.calls
        assert ("/board/goto", {"square": "4", "direction": "auto", "axis": "0"}) in bench.calls
        assert ("/board/goto", {"square": "7", "direction": "auto", "axis": "0"}) in bench.calls
        assert bench.squares[0] == 7          # Crystal Ball (Chance)


def test_player_1_takes_a_camera_roll_after_the_demo_moves():
    bench = FakeBench()
    moves = []

    class Recording(StubBridge):
        async def move(self, lane, frm, to):
            moves.append((lane, frm, to))
            return await super().move(lane, frm, to)

    with TestClient(create_app(
            sim=True, move_delay=0, dev=True, virtual_axis=0,
            bridge_factory=lambda: HttpBeltBridge("http://bench", lanes=0, axes={2: 0}, fallback=Recording(0),
                                                  transport=bench.transport()))) as c:
        assert c.post("/api/demo/resume").status_code == 200
        room = next(iter(c.app.state.rooms.values()))
        for _ in range(50):
            if [p.position for p in room.state.players] == [4, 7]:
                break
            time.sleep(0.1)
        assert [p.position for p in room.state.players] == [4, 7]
        r = c.post("/vision/events", json={"type": "dice_rolled", "values": [2, 3], "total": 5, "doubles": False})
        assert r.status_code == 200 and r.json()["sent"]
        for _ in range(50):
            if room.state.player("p1").position == 9:
                break
            time.sleep(0.1)
        assert room.state.player("p1").position == 9
        assert (1, 4, 9) in moves


def test_demo_resume_needs_dev():
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        assert c.post("/api/demo/resume").status_code == 404
