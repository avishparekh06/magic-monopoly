"""Grandma's lane moves through Avish's arduino/server.py, loaded unchanged, in one server."""
import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from server.adapters.bridge import ArduinoBridge, StubBridge
from server.hardware import motor_server
from server.main import create_app
from tests.test_ws import PLAYERS, recv_until, seat_token


@pytest.fixture
def hw(monkeypatch, tmp_path):
    """His module, fresh, in dry-run mode (no Arduino) with its state file in a temp dir."""
    monkeypatch.setenv("DRY_RUN", "1")
    module = motor_server(fresh=True)
    monkeypatch.setattr(module, "STATE_FILE", str(tmp_path / "board_state.json"))
    module.current_square = 0
    return module


@pytest.fixture
def client(hw):
    with TestClient(create_app(sim=True, move_delay=0, bridge="arduino")) as c:
        yield c


def test_his_endpoints_are_served_by_our_app(client):
    assert client.get("/board").json() == {"square": 0, "name": "GO", "is_corner": True}
    r = client.post("/board/goto", params={"square": 6, "direction": "forward"}).json()
    assert r["to"]["square"] == 6 and r["moved"] is False          # dry run: planned, not sent
    assert client.post("/board/set", params={"square": 2}).json()["square"] == 2
    assert client.post("/board/move", params={"squares": -1}).json()["to"]["square"] == 1
    assert client.post("/vision/events", json={"type": "dice_cleared"}).json()["ok"] is True
    # Our pages are still ours: his GET / (ui.html) is left out.
    assert "MONOPOLY" in client.get("/").text and "Belt Board" not in client.get("/").text


def test_stub_bridge_does_not_load_his_routes(hw):
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        assert c.get("/board").status_code == 404


def test_grandmas_game_move_goes_through_his_code(client, hw):
    g = client.post("/api/games", json={"players": PLAYERS}).json()
    gid = g["game_id"]
    with client.websocket_connect(f"/ws/{gid}?role=board") as board, \
         client.websocket_connect(f"/ws/{gid}?seat={seat_token(g, 'p2')}"), \
         client.websocket_connect(f"/ws/{gid}?seat={seat_token(g, 'p3')}"):
        board.send_json({"action": "start"})
        recv_until(board, lambda m: m["type"] == "turn_started")
        board.send_json({"source": "vision", "type": "dice", "value": 6})
        assert recv_until(board, lambda m: m["type"] == "offer")["square"] == 6
    assert client.get("/board").json()["square"] == 6               # his tracked square moved
    assert hw.current_square == 6


def run(coro):
    return asyncio.run(coro)


def test_kids_lanes_use_the_fallback(hw):
    bridge = ArduinoBridge(fallback=StubBridge(delay=0))
    assert run(bridge.move(2, 0, 9)) == {"type": "move_done", "lane": 2, "square": 9}
    assert hw.current_square == 0                                    # his token untouched


def test_respects_a_rehome_on_his_server(hw):
    hw.board_set(4)                                                  # token really on 4
    bridge = ArduinoBridge(fallback=StubBridge(delay=0))
    assert run(bridge.move(1, 0, 8))["square"] == 8                  # goes 4 -> 8 forward
    assert hw.current_square == 8


def test_waits_while_his_token_is_busy(hw):
    async def scenario():
        hw.lock.acquire()                                            # someone is jogging the token
        asyncio.get_running_loop().call_later(0.5, hw.lock.release)
        return await ArduinoBridge(fallback=StubBridge(delay=0)).move(1, 0, 3)
    assert run(scenario()) == {"type": "move_done", "lane": 1, "square": 3}


def test_motor_failure_becomes_move_failed(hw, monkeypatch):
    def broken(axis, steps):
        raise HTTPException(503, "Motor move failed: no reply")
    monkeypatch.setattr(hw, "drive", broken)
    result = run(ArduinoBridge(fallback=StubBridge(delay=0)).move(1, 0, 3))
    assert result == {"type": "move_failed", "lane": 1, "reason": "Motor move failed: no reply"}
    assert hw.current_square == 0                                    # position not advanced
