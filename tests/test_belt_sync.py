"""The physical side reports its square; in debug mode the game adopts it."""
from fastapi.testclient import TestClient

from server.adapters.bridge import StepBeltBridge
from server.main import create_app
from tests.test_step_bridge import FakeGateway
from tests.test_store import PLAYERS, recv_until, state_where

TABLE = [PLAYERS[0], dict(PLAYERS[1], kind="in_person")]


def test_push_shows_mismatch_but_only_adopts_in_debug_mode():
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        game = c.post("/api/games", json={"players": TABLE}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            recv_until(board, lambda m: m["type"] == "state")
            assert c.post("/belt/position", params={"lane": 1, "square": 14}).json()["ok"]
            m = recv_until(board, lambda m: m["type"] == "belt_position")
            assert m["square"] == 14 and m["game_square"] == 0           # shown, not adopted
            board.send_json({"action": "debug_sync", "on": True})
            s = state_where(board, lambda s: s["players"][0]["position"] == 14)   # adopted on enable
            c.post("/belt/position", params={"lane": 1, "square": 20})
            s = state_where(board, lambda s: s["players"][0]["position"] == 20)
            assert s["players"][1]["position"] == 0


def test_remote_push_needs_the_key():
    with TestClient(create_app(sim=True, move_delay=0, belt_key="k")) as c:
        c.post("/api/games", json={"players": TABLE})
        fwd = {"x-forwarded-for": "203.0.113.9"}
        assert c.post("/belt/position", params={"lane": 1, "square": 3}, headers=fwd).status_code == 403
        assert c.post("/belt/position", params={"lane": 1, "square": 3, "key": "k"}, headers=fwd).status_code == 200
        assert c.post("/belt/position", params={"lane": 1, "square": 40}).status_code == 422


def test_not_adopted_while_that_token_is_moving():
    g = FakeGateway(square=0)
    with TestClient(create_app(sim=True, move_delay=0, debug_sync=True,
                               bridge_factory=lambda: StepBeltBridge("https://belt.example", "k", transport=g.transport()))) as c:
        game = c.post("/api/games", json={"players": TABLE}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            recv_until(board, lambda m: m["type"] == "debug_sync" and m["on"])
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            c.post("/vision/events", json={"type": "dice_rolled", "values": [3, 3], "total": 6})
            s = state_where(board, lambda s: s["players"][0]["position"] == 6 and s["phase"] != "MOVING")
            assert g.square == 6
            # landed on an unowned place: while the offer is pending the square is part of the action,
            # so a belt report is shown but not adopted
            assert s["phase"] == "WAITING_FOR_DECISION"
            c.post("/belt/position", params={"lane": 1, "square": 9, "game": game["game_id"]})
            m = recv_until(board, lambda m: m["type"] == "belt_position" and m["square"] == 9)
            assert m["game_square"] == 6
            board.send_json({"action": "decision", "player": "p1", "answer": "no"})
            s = state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            assert s["players"][0]["position"] == 6
            # between turns the game follows the physical side
            c.post("/belt/position", params={"lane": 1, "square": 9, "game": game["game_id"]})
            state_where(board, lambda s: s["players"][0]["position"] == 9)


def test_game_start_adopts_where_the_belt_is():
    g = FakeGateway(square=14)
    with TestClient(create_app(sim=True, move_delay=0,
                               bridge_factory=lambda: StepBeltBridge("https://belt.example", "k", transport=g.transport()))) as c:
        game = c.post("/api/games", json={"players": TABLE}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            recv_until(board, lambda m: m["type"] == "state")
            board.send_json({"action": "start", "force": True})
            s = state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL" and s["players"][0]["position"] == 14)
            assert s["players"][1]["position"] == 0          # only the lane the belt reports


def test_the_virtual_lane_is_polled_and_adopted_too():
    from server.adapters.bridge import HttpBeltBridge
    from tests.test_http_bridge import FakeBelt
    g, belt = FakeGateway(square=5), FakeBelt()
    belt.square[0] = 9
    def bridge():
        virtual = HttpBeltBridge("http://bench:8001", lanes=0, axes={2: 0}, transport=belt.transport())
        return StepBeltBridge("https://belt.example", "k", fallback=virtual, transport=g.transport())
    with TestClient(create_app(sim=True, move_delay=0, debug_sync=True, bridge_factory=bridge)) as c:
        game = c.post("/api/games", json={"players": TABLE}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            state_where(board, lambda s: s["players"][0]["position"] == 5 and s["players"][1]["position"] == 9)
