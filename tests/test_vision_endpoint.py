"""The camera process POSTs raw vision events; the server maps them onto engine actions."""
from fastapi.testclient import TestClient

from server.main import create_app, translate_vision_event
from tests.test_store import PLAYERS, recv_until, state_where


def test_translation():
    assert translate_vision_event({"type": "dice_rolled", "values": [3, 4], "total": 7, "doubles": False}) == {"type": "dice", "value": 7}
    assert translate_vision_event({"type": "dice_unclear", "reason": "touching", "seen": []}) == {"type": "dice_not_visible"}
    assert translate_vision_event({"type": "card_drawn", "card_id": "crystal_08", "score": 40}) == {"type": "card", "id": "crystal_08"}
    assert translate_vision_event({"type": "dice_cleared"}) is None
    assert translate_vision_event({"type": "card_removed"}) is None


def test_dice_event_drives_the_game():
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        # nothing running yet: lobby games don't count as live
        assert c.post("/vision/events", json={"type": "dice_rolled", "values": [3, 4], "total": 7}).status_code == 409
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            recv_until(board, lambda m: m["type"] == "state")
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            r = c.post("/vision/events", json={"type": "dice_rolled", "values": [3, 4], "total": 7, "doubles": False})
            assert r.status_code == 200 and r.json()["sent"] == {"type": "dice", "value": 7}
            s = state_where(board, lambda s: s["phase"] == "WAITING_FOR_CARD")
            assert s["players"][0]["position"] == 7
            # a card read while a card is wanted
            c.post("/vision/events", json={"type": "card_drawn", "card_id": "crystal_08", "score": 50})
            s = state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            assert s["players"][0]["balance"] == 1550
            # on the remote player's turn table dice don't count (they roll on their phone): rejected, not applied
            r = c.post("/vision/events", json={"type": "dice_rolled", "values": [1, 1], "total": 2})
            assert r.status_code == 200
            err = recv_until(board, lambda m: m["type"] == "error")
            assert "phone" in err["message"]
            assert c.app.state.rooms[game["game_id"]].state.players[1].position == 0
            # explicit game id works too; unknown id is 404; malformed body is 422
            assert c.post(f"/vision/events?game={game['game_id']}", json={"type": "dice_cleared"}).json()["ignored"] == "dice_cleared"
            assert c.post("/vision/events?game=nope", json={"type": "dice_rolled", "total": 7}).status_code == 404
            assert c.post("/vision/events", json={"type": "dice_rolled"}).status_code == 422


def test_remote_callers_are_refused():
    """Through a tunnel every request arrives from 127.0.0.1 but carries a forwarding header."""
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        fwd = {"x-forwarded-for": "203.0.113.9"}
        assert c.post("/vision/events", json={"type": "dice_rolled", "total": 7}, headers=fwd).status_code == 403
        assert c.post("/vision/events", json={"type": "dice_rolled", "total": 7}, headers={"cf-connecting-ip": "203.0.113.9"}).status_code == 403
        # the games list hides host links from remote callers
        assert "host_url" in c.get("/api/games").json()[0]
        assert "host_url" not in c.get("/api/games", headers=fwd).json()[0]


def test_events_go_to_the_game_being_played():
    """Two running games (e.g. one abandoned earlier): the camera drives the most recently active one."""
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        ids = []
        for _ in range(2):
            game = c.post("/api/games", json={"players": PLAYERS}).json()
            with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
                recv_until(board, lambda m: m["type"] == "state")
                board.send_json({"action": "start", "force": True})
                state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            ids.append(game["game_id"])
        r = c.post("/vision/events", json={"type": "dice_rolled", "total": 7})
        assert r.status_code == 200 and r.json()["game"] == ids[1]


def test_mode_tells_the_camera_what_to_look_for():
    """One camera process: dice by default, the card zone while the game waits for a drawn card."""
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        assert c.get("/vision/mode").json() == {"mode": "dice", "game": None, "phase": None, "detail": "no game running"}
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        assert c.get("/vision/mode").json()["game"] is None            # lobby games are not being played yet
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            recv_until(board, lambda m: m["type"] == "state")
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            m = c.get("/vision/mode").json()
            assert m["mode"] == "dice" and m["game"] == game["game_id"] and m["phase"] == "WAITING_FOR_ROLL"
            c.post("/vision/events", json={"type": "dice_rolled", "values": [3, 4], "total": 7, "doubles": False})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_CARD")
            m = c.get("/vision/mode").json()
            assert m["mode"] == "card" and m["deck"] == "crystal" and "Crystal Ball" in m["detail"]
            assert m["player"] == PLAYERS[0]["name"]
            c.post("/vision/events", json={"type": "card_drawn", "card_id": "crystal_08", "score": 50})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            assert c.get("/vision/mode").json()["mode"] == "dice"
            assert c.get(f"/vision/mode?game={game['game_id']}").json()["game"] == game["game_id"]
            assert c.get("/vision/mode?game=nope").status_code == 404
        assert c.get("/vision/mode", headers={"x-forwarded-for": "203.0.113.9"}).status_code == 403


def test_camera_heartbeat_reaches_the_board():
    """The camera process reports what it sees; the board page gets one "camera" message per change,
    and the reply tells the camera what to look for."""
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            first = recv_until(board, lambda m: m["type"] == "camera")
            assert first == {"type": "camera", "online": False, "ever": False}      # no camera has reported yet
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            r = c.post("/vision/sync", json={"mode": "dice", "dice_state": "ROLLING", "seeing": "touching", "fps": 29})
            assert r.status_code == 200 and r.json()["mode"] == "dice"
            m = recv_until(board, lambda m: m["type"] == "camera")
            assert m["online"] and m["ever"] and m["mode"] == "dice" and m["dice_state"] == "ROLLING" and m["seeing"] == "touching"
            # the same report again is not re-broadcast; a changed one is
            c.post("/vision/sync", json={"mode": "dice", "dice_state": "ROLLING", "seeing": "touching", "fps": 29})
            c.post("/vision/sync", json={"mode": "dice", "dice_state": "READ", "seeing": "3 + 4", "fps": 29})
            m = recv_until(board, lambda m: m["type"] == "camera")
            assert m["dice_state"] == "READ" and m["seeing"] == "3 + 4"
            # a board page that connects later gets the current camera picture straight away
            with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as second:
                assert recv_until(second, lambda m: m["type"] == "camera")["seeing"] == "3 + 4"
        assert c.post("/vision/sync", json={"mode": "dice"}, headers={"x-forwarded-for": "203.0.113.9"}).status_code == 403


def test_camera_goes_offline_when_reports_stop():
    from server.camera import CameraStatus
    cam = CameraStatus()
    assert cam.snapshot() == {"type": "camera", "online": False, "ever": False}
    cam.update({"mode": "dice", "dice_state": "EMPTY", "seeing": "empty", "ignored": 1})
    old = cam.snapshot()
    assert old["ever"] and "ignored" not in old and old["mode"] == "dice"
    assert cam.changed() is not None and cam.changed() is None      # only the first hand-out counts as a change
    cam.seen_at = 0.0                                                 # long ago
    gone = cam.changed()
    assert gone is not None and gone["online"] is False and gone["ever"] is True
