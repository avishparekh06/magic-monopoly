"""The camera process POSTs raw vision events; the server maps them onto engine actions."""
from fastapi.testclient import TestClient

from server.main import create_app, translate_vision_event
from tests.test_store import PLAYERS, recv_until, state_where


def test_translation():
    assert translate_vision_event({"type": "dice_rolled", "values": [3, 4], "total": 7, "doubles": False}) == {"type": "dice", "value": 7}
    assert translate_vision_event({"type": "dice_unclear", "reason": "touching", "seen": []}) == {"type": "dice_not_visible"}
    assert translate_vision_event({"type": "card_drawn", "card_id": "spell_08", "score": 40}) == {"type": "card", "id": "spell_08"}
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
            c.post("/vision/events", json={"type": "card_drawn", "card_id": "spell_08", "score": 50})
            s = state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            assert s["players"][0]["balance"] == 1550
            # a stray roll on a remote player's turn is rejected by the engine, not applied
            r = c.post("/vision/events", json={"type": "dice_rolled", "values": [1, 1], "total": 2})
            assert r.status_code == 200          # delivered; the engine's rejection goes to the board page
            err = recv_until(board, lambda m: m["type"] == "error")
            assert "turn" in err["message"]
            # explicit game id works too; unknown id is 404
            assert c.post(f"/vision/events?game={game['game_id']}", json={"type": "dice_cleared"}).json()["ignored"] == "dice_cleared"
            assert c.post("/vision/events?game=nope", json={"type": "dice_rolled", "total": 7}).status_code == 404
