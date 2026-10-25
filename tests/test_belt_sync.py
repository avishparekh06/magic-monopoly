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
            # a stale report for a token that is not moving is adopted; the game follows the physical side
            c.post("/belt/position", params={"lane": 1, "square": 9, "game": game["game_id"]})
            state_where(board, lambda s: s["players"][0]["position"] == 9)
