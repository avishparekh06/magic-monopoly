"""Demo mode: jog Player 2's belt through the belt tool, then Resume (demo) plays the scripted moves."""
import asyncio
import time

import httpx
from fastapi.testclient import TestClient

from server.adapters.bridge import HttpBeltBridge, MoveBridge, StubBridge
from server.adapters import make_adapters
from server.demo import DEMO_FORCED_ROLLS, DEMO_MOVES, DEMO_PLAYERS, is_fresh_demo
from server.engine import SYSTEM
from server.rooms import GameRoom
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
        jogs = [(q.get("steps"), q["axis"]) for p, q in bench.calls if p == "/jog"]
        assert jogs == [("50", "0"), ("-50", "0")]
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


def demo_room(c):
    """The pre-made demo game: a saved, started game that has not been played yet."""
    rooms = [r for r in c.app.state.rooms.values() if is_fresh_demo(r.state)]
    assert len(rooms) == 1
    return rooms[0]


def open_board(c, room):
    """Open the demo game's board the way the laptop does; its scripted belt moves start by themselves."""
    return c.websocket_connect(f"/ws/{room.game_id}?role=board")


def wait_for(cond, tries=60):
    for _ in range(tries):
        if cond():
            return
        time.sleep(0.1)


def test_a_demo_game_is_ready_like_any_saved_game():
    assert [(p["name"], p["position"]) for p in DEMO_PLAYERS] == [("Player 1", 3), ("Player 2", 1)]
    assert DEMO_MOVES == [("p2", 3), ("p1", 3)]
    bench = FakeBench()
    with app_client(bench) as c:
        room = demo_room(c)
        assert room.state.phase.value == "WAITING_FOR_ROLL" and room.forced_rolls == {"p2": [(1, 2)]}
        games = c.get("/api/games").json()
        assert [g["game_id"] for g in games] == [room.game_id] and games[0]["host_url"]   # shows under Continue game
        assert c.get("/api/config").json()["demo"] is True
    with TestClient(create_app(sim=True, move_delay=0, dev=True)) as c:                    # no belt wiring: no demo game
        assert c.get("/api/games").json() == [] and c.get("/api/config").json()["demo"] is False


def test_opening_the_board_plays_the_scripted_moves_on_the_belt():
    bench = FakeBench()
    with app_client(bench) as c:
        room = demo_room(c)
        assert not [1 for p, q in bench.calls if p == "/board/goto"]          # nothing moves until the board opens
        with open_board(c, room):
            wait_for(lambda: [p.position for p in room.state.players] == [6, 4])
            assert [p.position for p in room.state.players] == [6, 4]
            assert room.state.phase.value == "WAITING_FOR_ROLL"
            # Player 2's belt (axis 0 here) was told it starts on Black Forest, then driven to square 4
            assert ("/board/set", {"square": "1", "axis": "0"}) in bench.calls
            assert ("/board/goto", {"square": "4", "direction": "auto", "axis": "0"}) in bench.calls
            assert bench.squares[0] == 4
        with open_board(c, room):                                             # reopening does not play it again
            time.sleep(0.3)
        assert len([1 for p, q in bench.calls if p == "/board/goto"]) == 1


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
        room = demo_room(c)
        with open_board(c, room):
            wait_for(lambda: [p.position for p in room.state.players] == [6, 4])
            assert [p.position for p in room.state.players] == [6, 4]
            r = c.post("/vision/events", json={"type": "dice_rolled", "values": [2, 3], "total": 5, "doubles": False})
            assert r.status_code == 200 and r.json()["sent"]
            wait_for(lambda: room.state.player("p1").position == 11)
            assert room.state.player("p1").position == 11
            assert (1, 6, 11) in moves


def test_player_2s_first_roll_is_three():
    async def go():
        room = await GameRoom.create(DEMO_PLAYERS, make_adapters(True, 0), 7, None)
        room.forced_rolls = {pid: list(r) for pid, r in DEMO_FORCED_ROLLS.items()}
        await room.dispatch(SYSTEM, {"action": "start", "force": True})
        room.state.current = 1                                                # Player 2's turn
        events = await room.dispatch("p2", {"action": "roll"})
        return room, events

    room, events = asyncio.run(go())
    rolled = next(e for e in events if e["type"] == "rolled")
    assert rolled["value"] == 3 and rolled["dice"] == [1, 2]
    assert room.forced_rolls["p2"] == []                                      # used up: later rolls are random


def test_the_board_offers_each_remote_player_an_invite_link():
    with app_client(FakeBench()) as c:
        room = demo_room(c)
        players = c.get(f"/api/games/{room.game_id}/invites").json()["players"]
        assert [p["id"] for p in players] == ["p2"]
        assert players[0]["url"].endswith(f"/game/{room.game_id}/join/{next(iter(room.seat_tokens))}")
        assert c.get("/api/games/nope/invites").status_code == 404


def test_demo_resume_endpoint_needs_dev():
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        assert c.post("/api/demo/resume").status_code == 404


def test_two_lanes_on_the_belt_tool_axes_0_and_2(monkeypatch):
    """Player 1 drives axis 0, Player 2 axis 2: the jog buttons, the starts and the scripted moves all follow."""
    bench = FakeBench()
    import server.jog as jog
    real = jog.httpx.AsyncClient
    monkeypatch.setattr(jog.httpx, "AsyncClient", lambda **kw: real(**{**kw, "transport": bench.transport()}))
    app = create_app(sim=True, move_delay=0, dev=True, bridge="http", lane_axes={1: 0, 2: 2},
                     bridge_factory=lambda: HttpBeltBridge("http://bench", lanes=0, axes={1: 0, 2: 2},
                                                           fallback=StubBridge(0), transport=bench.transport()))
    with TestClient(app) as c:
        assert c.get("/api/config").json()["jog_players"] == [1, 2]
        assert c.post("/jog/nudge", params={"steps": 50, "player": 1}).status_code == 200
        assert c.post("/jog/nudge", params={"steps": 50, "player": 2}).status_code == 200
        assert c.post("/jog/nudge", params={"steps": 50, "player": 3}).status_code == 422
        assert [q["axis"] for p, q in bench.calls if p == "/jog"] == ["0", "2"]
        room = demo_room(c)
        bench.calls.clear()
        with open_board(c, room):
            wait_for(lambda: [p.position for p in room.state.players] == [6, 4])
            assert [p.position for p in room.state.players] == [6, 4]
            gotos = [(q["axis"], q["square"]) for p, q in bench.calls if p == "/board/goto"]
            assert gotos == [("2", "4"), ("0", "6")]
            assert ("/board/set", {"square": "3", "axis": "0"}) in bench.calls
            assert ("/board/set", {"square": "1", "axis": "2"}) in bench.calls
