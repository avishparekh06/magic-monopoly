"""Debug alignment: start positions, set_position, and syncing lane 1 from the belt gateway."""
import httpx
import pytest
from fastapi.testclient import TestClient

from server.adapters.bridge import StepBeltBridge
from server.engine import SYSTEM, EngineError, Phase, apply_action, new_game
from server.main import create_app
from tests.test_step_bridge import FakeGateway
from tests.test_store import PLAYERS, recv_until, state_where


def test_new_game_accepts_start_positions():
    specs = [dict(PLAYERS[0], position=14), PLAYERS[1]]
    s = new_game("g", specs)
    assert [p.position for p in s.players] == [14, 0]
    with pytest.raises(EngineError):
        new_game("g", [dict(PLAYERS[0], position=40), PLAYERS[1]])


def test_set_position_moves_nothing_else():
    s = new_game("g", PLAYERS)
    for p in s.players:
        p.connected = True
    s, _ = apply_action(s, SYSTEM, {"action": "start"})
    s, events = apply_action(s, SYSTEM, {"action": "set_position", "player": "p1", "square": 36})
    assert s.player("p1").position == 36 and s.player("p1").balance == 1500
    assert s.phase == Phase.WAITING_FOR_ROLL and events == [{"type": "position_set", "player": "p1", "from": 0, "to": 36}]
    with pytest.raises(EngineError, match="only the game host"):
        apply_action(s, "p1", {"action": "set_position", "player": "p1", "square": 3})
    # a roll from 36 wraps and pays salary as usual
    s, _ = apply_action(s, SYSTEM, {"action": "dice", "value": 6})
    assert s.pending["to"] == 2


def test_create_with_position_and_sync_from_belt():
    g = FakeGateway(square=36)
    with TestClient(create_app(sim=True, move_delay=0,
                               bridge_factory=lambda: StepBeltBridge("https://belt.example", "k", transport=g.transport()))) as c:
        specs = [dict(PLAYERS[0], position=12), dict(PLAYERS[1], kind="in_person")]
        game = c.post("/api/games", json={"players": specs}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            s = recv_until(board, lambda m: m["type"] == "state")["state"]
            assert s["players"][0]["position"] == 12
            board.send_json({"action": "sync_belt"})                 # belt says 36: lane 1 follows it
            s = state_where(board, lambda s: s["players"][0]["position"] == 36)
            assert s["players"][1]["position"] == 0                  # lane 2 is not on the belt
            board.send_json({"action": "set_position", "player": "p2", "square": 7})
            s = state_where(board, lambda s: s["players"][1]["position"] == 7)
            # start and roll: the step goes from the belt's real square (36) to the target
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            c.post("/vision/events", json={"type": "dice_rolled", "values": [2, 2], "total": 4})
            s = state_where(board, lambda s: s["players"][0]["position"] == 0 and s["phase"] != "MOVING")
            assert g.square == 0 and s["players"][0]["balance"] == 1700           # 36 + 4 = 0, salary paid


def test_seat_page_cannot_sync():
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        token = game["join_links"]["p2"].rsplit("/", 1)[1]
        with c.websocket_connect(f"/ws/{game['game_id']}?seat={token}") as maya:
            recv_until(maya, lambda m: m["type"] == "state")
            maya.send_json({"action": "sync_belt"})
            assert "board or host" in recv_until(maya, lambda m: m["type"] == "error")["message"]
            maya.send_json({"action": "set_position", "player": "p2", "square": 9})
            assert "host" in recv_until(maya, lambda m: m["type"] == "error")["message"]
