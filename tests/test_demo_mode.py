"""Demo mode: jog the belts through the belt tool, resume the demo game, play its forced rolls, reset it."""
import asyncio
import time

import httpx
from fastapi.testclient import TestClient

from server.adapters.bridge import HttpBeltBridge, MoveBridge, StubBridge
from server.adapters import make_adapters
from server.demo import DEMO_FIRST_PLAYER, DEMO_FORCED_ROLLS, DEMO_MOVES, DEMO_PLAYERS, is_fresh_demo
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
    """Open the demo game's board the way the laptop does."""
    return c.websocket_connect(f"/ws/{room.game_id}?role=board")


def wait_for(cond, tries=60):
    for _ in range(tries):
        if cond():
            return
        time.sleep(0.1)


def two_lane_app(bench, **kw):
    return create_app(sim=True, move_delay=0, dev=True, bridge="http", lane_axes={1: 0, 2: 2},
                      bridge_factory=lambda: HttpBeltBridge("http://bench", lanes=0, axes={1: 0, 2: 2},
                                                            fallback=StubBridge(0), transport=bench.transport()), **kw)


def sets(bench, axis, square):
    return [1 for p, q in bench.calls if p == "/board/set" and q == {"square": str(square), "axis": str(axis)}]


def gotos(bench):
    return [(q["axis"], q["square"]) for p, q in bench.calls if p == "/board/goto"]


def test_a_demo_game_is_ready_like_any_saved_game():
    assert [(p["name"], p["position"]) for p in DEMO_PLAYERS] == [("Player 1", 3), ("Player 2", 1)]
    assert DEMO_MOVES == [] and DEMO_FIRST_PLAYER == "p1"
    with TestClient(two_lane_app(FakeBench())) as c:
        room = demo_room(c)
        assert room.state.phase.value == "WAITING_FOR_ROLL" and room.state.current_player.id == "p1"
        assert room.forced_rolls == {"p1": [(1, 1)], "p2": [(2, 4)]}
        games = c.get("/api/games").json()
        assert [g["game_id"] for g in games] == [room.game_id] and games[0]["host_url"]   # shows under Continue game
        assert c.get("/api/config").json()["demo"] is True
    with TestClient(create_app(sim=True, move_delay=0, dev=True)) as c:                    # no belt wiring: no demo game
        assert c.get("/api/games").json() == [] and c.get("/api/config").json()["demo"] is False


def test_opening_the_board_tells_the_belt_where_the_tokens_are_and_moves_nothing():
    bench = FakeBench()
    with TestClient(two_lane_app(bench)) as c:
        room = demo_room(c)
        assert not sets(bench, 0, 3)
        with open_board(c, room):
            wait_for(lambda: sets(bench, 0, 3) and sets(bench, 2, 1))
            assert sets(bench, 0, 3) and sets(bench, 2, 1)       # Player 1 on Gingerbread House, Player 2 on Black Forest
            assert gotos(bench) == [] and [p.position for p in room.state.players] == [3, 1]
        with open_board(c, room):                                 # reopening does not tell it again
            time.sleep(0.3)
        assert len(sets(bench, 0, 3)) == 1


def test_the_whole_demo_works_forced_rolls_then_a_real_roll():
    """P1 clicks Roll Dice (forced 2: Gingerbread House -> Geppetto's), declines the offer; P2 rolls on the phone
    (forced 6: Black Forest -> Crystal Ball) and the card is read; then P1's real roll comes from the camera."""
    bench = FakeBench()
    with TestClient(two_lane_app(bench)) as c:
        room = demo_room(c)
        with open_board(c, room) as board:
            wait_for(lambda: sets(bench, 2, 1))
            # 1. Player 1, from the board's Roll Dice button
            board.send_json({"action": "board_roll", "target": "p1", "turn": 1})
            wait_for(lambda: room.state.phase.value == "WAITING_FOR_DECISION")
            assert room.state.player("p1").position == 5 and room.state.last_roll == 2
            assert gotos(bench) == [("0", "5")]
            board.send_json({"action": "decision", "answer": "no"})
            wait_for(lambda: room.state.current_player.id == "p2" and room.state.phase.value == "WAITING_FOR_ROLL")
            assert room.state.current_player.id == "p2"
            # 2. Player 2, on the phone
            token = next(tok for tok, pid in room.seat_tokens.items() if pid == "p2")
            with c.websocket_connect(f"/ws/{room.game_id}?seat={token}") as phone:
                phone.send_json({"action": "roll"})
                wait_for(lambda: room.state.phase.value == "WAITING_FOR_CARD")
            assert room.state.player("p2").position == 7 and room.state.last_roll == 6
            assert gotos(bench) == [("0", "5"), ("2", "7")]
            board.send_json({"action": "card", "id": "crystal_08"})              # the drawn Crystal Ball card (collect 50)
            wait_for(lambda: room.state.current_player.id == "p1" and room.state.phase.value == "WAITING_FOR_ROLL")
            assert room.state.player("p2").balance == 1550 and room.state.current_player.id == "p1"
            # 3. Player 1 rolls the real dice: the camera reads a 3
            r = c.post("/vision/events", json={"type": "dice_rolled", "values": [1, 2], "total": 3, "doubles": False})
            assert r.status_code == 200 and r.json()["sent"]
            wait_for(lambda: room.state.player("p1").position == 8)
            assert room.state.player("p1").position == 8 and gotos(bench)[-1] == ("0", "8")
            assert room.forced_rolls == {"p1": [], "p2": []}                       # the forced rolls are used up


def test_the_forced_rolls_are_fixed_and_used_up_once():
    async def first_roll(first, who, action):
        room = await GameRoom.create(DEMO_PLAYERS, make_adapters(True, 0), 7, None)
        room.forced_rolls = {pid: list(r) for pid, r in DEMO_FORCED_ROLLS.items()}
        await room.dispatch(SYSTEM, {"action": "start", "force": True, "first": first})
        events = await room.dispatch(*action(room))
        return room, next(e for e in events if e["type"] == "rolled")

    room, rolled = asyncio.run(first_roll("p1", "p1", lambda r: (SYSTEM, {"action": "board_roll", "target": "p1", "turn": 1})))
    assert rolled["value"] == 2 and rolled["dice"] == [1, 1] and room.forced_rolls["p1"] == []
    room, rolled = asyncio.run(first_roll("p2", "p2", lambda r: ("p2", {"action": "roll"})))
    assert rolled["value"] == 6 and rolled["dice"] == [2, 4] and room.forced_rolls["p2"] == []


def test_the_board_offers_each_remote_player_an_invite_link():
    with TestClient(two_lane_app(FakeBench())) as c:
        room = demo_room(c)
        players = c.get(f"/api/games/{room.game_id}/invites").json()["players"]
        assert [p["id"] for p in players] == ["p2"]
        assert players[0]["url"].endswith(f"/game/{room.game_id}/join/{next(iter(room.seat_tokens))}")
        assert c.get("/api/games/nope/invites").status_code == 404


def test_demo_resume_endpoint_needs_dev():
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        assert c.post("/api/demo/resume").status_code == 404


def test_the_jog_buttons_and_the_lineup_follow_axes_0_and_2(monkeypatch):
    """Player 1 drives axis 0, Player 2 axis 2: the jog buttons and the starts all follow."""
    bench = FakeBench()
    import server.jog as jog
    real = jog.httpx.AsyncClient
    monkeypatch.setattr(jog.httpx, "AsyncClient", lambda **kw: real(**{**kw, "transport": bench.transport()}))
    with TestClient(two_lane_app(bench)) as c:
        assert c.get("/api/config").json()["jog_players"] == [1, 2]
        assert c.post("/jog/nudge", params={"steps": 50, "player": 1}).status_code == 200
        assert c.post("/jog/nudge", params={"steps": 50, "player": 2}).status_code == 200
        assert c.post("/jog/nudge", params={"steps": 50, "player": 3}).status_code == 422
        assert [q["axis"] for p, q in bench.calls if p == "/jog"] == ["0", "2"]
        assert c.post("/jog/home", params={"square": 3, "player": 1}).status_code == 200
        assert sets(bench, 0, 3)


def test_a_played_demo_game_is_replayed_fresh_after_a_restart(tmp_path):
    """The demo ignores saved state: old games do not touch the belt at startup, and opening a demo game that was
    played before the restart gives a fresh demo from the lined-up squares instead."""
    db = str(tmp_path / "games.db")
    bench = FakeBench()
    with TestClient(two_lane_app(bench, db_path=db)) as c:
        old = demo_room(c)
        with open_board(c, old) as board:
            board.send_json({"action": "board_roll", "target": "p1", "turn": 1})
            wait_for(lambda: old.state.player("p1").position == 5)
        old_id = old.game_id
        assert old.state.player("p1").position == 5

    bench2 = FakeBench()
    with TestClient(two_lane_app(bench2, db_path=db)) as c:
        assert not sets(bench2, 0, 5)                  # startup did not push the old game's squares to the belt tool
        r = c.get(f"/board/{old_id}", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] != f"/board/{old_id}"
        new_id = r.headers["location"].rsplit("/", 1)[1]
        fresh = c.app.state.rooms[new_id]
        assert is_fresh_demo(fresh.state) and fresh.forced_rolls == {"p1": [(1, 1)], "p2": [(2, 4)]}
        assert c.get(f"/board/{new_id}", follow_redirects=False).status_code == 200
        with open_board(c, fresh) as board:
            board.send_json({"action": "board_roll", "target": "p1", "turn": 1})
            wait_for(lambda: fresh.state.player("p1").position == 5)
        assert c.get(f"/board/{new_id}", follow_redirects=False).status_code == 200   # a running game is not replaced


def test_muse_can_be_switched_on_and_off(monkeypatch):
    from server.adapters.mirror import MuseMirror, TemplateMirror
    from server.adapters.voice import MuseVoice, StubVoice
    with TestClient(two_lane_app(FakeBench())) as c:
        room = demo_room(c)
        assert c.get("/api/config").json()["muse"] is False and isinstance(room.adapters.voice, StubVoice)
        monkeypatch.delenv("MUSE_API_KEY", raising=False)
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        assert c.post("/api/muse", params={"on": "true"}).status_code == 400            # no key: nothing changes
        assert c.get("/api/config").json()["muse"] is False
        monkeypatch.setenv("MUSE_API_KEY", "k")
        assert c.post("/api/muse", params={"on": "true"}).json() == {"muse": True}
        cfg = c.get("/api/config").json()
        assert cfg["muse"] is True and cfg["voice"] == "muse"
        assert isinstance(room.adapters.voice, MuseVoice) and isinstance(room.adapters.mirror, MuseMirror)
        assert c.post("/api/muse", params={"on": "false"}).json() == {"muse": False}
        assert type(room.adapters.mirror) is TemplateMirror and isinstance(room.adapters.voice, StubVoice)
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        assert c.post("/api/muse", params={"on": "true"}).status_code == 404            # not in demo mode


def test_arrows_nudge_a_token_forward_and_back():
    bench = FakeBench()
    with TestClient(two_lane_app(bench)) as c:
        room = demo_room(c)
        with open_board(c, room):
            wait_for(lambda: sets(bench, 2, 1))
            r = c.post("/api/demo/step", params={"game": room.game_id, "player": "p1", "squares": 1})
            assert r.json() == {"player": "p1", "square": 4} and room.state.player("p1").position == 4
            assert c.post("/api/demo/step", params={"game": room.game_id, "player": "p1", "squares": -1}).status_code == 200
            assert room.state.player("p1").position == 3
            assert gotos(bench) == [("0", "4"), ("0", "3")]
            assert c.post("/api/demo/step", params={"game": room.game_id, "player": "p2", "squares": -1}).json()["square"] == 0
            assert c.post("/api/demo/step", params={"game": room.game_id, "player": "p1", "squares": 5}).status_code == 422
            assert c.post("/api/demo/step", params={"game": room.game_id, "player": "p9", "squares": 1}).status_code == 422


def test_reset_drives_the_tokens_home_and_starts_a_fresh_demo():
    bench = FakeBench()
    with TestClient(two_lane_app(bench)) as c:
        old = demo_room(c)
        with open_board(c, old) as board:
            wait_for(lambda: sets(bench, 2, 1))
            board.send_json({"action": "board_roll", "target": "p1", "turn": 1})
            wait_for(lambda: old.state.phase.value == "WAITING_FOR_DECISION")
            board.send_json({"action": "decision", "answer": "no"})
            wait_for(lambda: old.state.current_player.id == "p2")
        bench.calls.clear()
        r = c.post("/api/demo/reset", params={"game": old.game_id}).json()
        assert r["game_id"] != old.game_id and r["board_url"] == f"/board/{r['game_id']}"
        assert gotos(bench) == [("0", "3")]                       # Player 1 drove back 5 -> 3; Player 2 never left square 1
        fresh = c.app.state.rooms[r["game_id"]]
        assert is_fresh_demo(fresh.state) and fresh.forced_rolls == {"p1": [(1, 1)], "p2": [(2, 4)]}
        assert old.game_id not in c.app.state.rooms
        status = {g["game_id"]: g["status"] for g in c.get("/api/games").json()}
        assert status[old.game_id] == "abandoned" and status[r["game_id"]] == "active"
