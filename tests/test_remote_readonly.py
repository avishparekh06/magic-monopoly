"""Through the public tunnel the board page only watches; games are created on the laptop."""
from fastapi.testclient import TestClient

from server.main import create_app
from tests.test_store import PLAYERS, recv_until

FWD = {"x-forwarded-for": "203.0.113.9"}


def test_remote_board_is_read_only_and_cannot_create():
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        assert c.post("/api/games", json={"players": PLAYERS}, headers=FWD).status_code == 403
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board", headers=FWD) as judge:
            hello = recv_until(judge, lambda m: m["type"] == "hello")
            assert hello["role"] == "viewer"
            recv_until(judge, lambda m: m["type"] == "state")
            judge.send_json({"action": "start", "force": True})
            assert "read-only" in recv_until(judge, lambda m: m["type"] == "error")["message"]
            judge.send_json({"source": "vision", "type": "dice", "value": 7})
            assert "read-only" in recv_until(judge, lambda m: m["type"] == "error")["message"]
        # the laptop's board page still acts as the host
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            assert recv_until(board, lambda m: m["type"] == "hello")["role"] == "board"
