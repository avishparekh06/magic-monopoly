"""End-to-end: HTTP create + WebSocket join, takeover, broadcast, spoofing."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.main import create_app
from server.rooms import TAKEN_OVER

PLAYERS = [
    {"name": "Grandma", "kind": "in_person"},
    {"name": "Maya", "kind": "remote"},
    {"name": "Leo", "kind": "remote"},
]


@pytest.fixture
def client():
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        yield c


@pytest.fixture
def game(client):
    r = client.post("/api/games", json={"players": PLAYERS})
    assert r.status_code == 200
    return r.json()


def seat_token(game, pid):
    return game["join_links"][pid].rsplit("/", 1)[1]


def recv_until(ws, pred, limit=30):
    """Read messages until one matches pred; return it."""
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("expected message never arrived")


def is_state(msg):
    return msg["type"] == "state"


def connected(msg, pid):
    return next(p for p in msg["state"]["players"] if p["id"] == pid)["connected"]


def test_create_returns_links(client, game):
    assert set(game["join_links"]) == {"p2", "p3"}
    assert client.get(game["join_links"]["p2"]).status_code == 200      # kid page served
    assert client.get(game["board_url"]).status_code == 200
    r = client.get(f"/api/games/{game['game_id']}/links", params={"host": game["host_token"]})
    assert r.json() == game
    r = client.get(f"/api/games/{game['game_id']}/links", params={"host": "nope"})
    assert r.status_code == 403


def test_bad_roster_is_400(client):
    r = client.post("/api/games", json={"players": PLAYERS[1:]})
    assert r.status_code == 400


def test_bad_token_and_unknown_game_rejected(client, game):
    with client.websocket_connect(f"/ws/{game['game_id']}?seat=wrong") as ws:
        assert ws.receive_json()["fatal"] is True
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with client.websocket_connect("/ws/nosuchgame?role=board") as ws:
        assert "doesn't exist" in ws.receive_json()["message"]


def test_join_broadcasts_to_everyone_and_start(client, game):
    gid = game["game_id"]
    with client.websocket_connect(f"/ws/{gid}?host={game['host_token']}") as host, \
         client.websocket_connect(f"/ws/{gid}?role=board") as board:
        for v in (host, board):
            assert v.receive_json()["type"] == "hello"
            assert not connected(v.receive_json(), "p2")

        with client.websocket_connect(f"/ws/{gid}?seat={seat_token(game, 'p2')}") as maya:
            assert maya.receive_json() == {"type": "hello", "you": "p2", "role": "seat"}
            for ws in (maya, host, board):
                assert connected(recv_until(ws, is_state), "p2")

            # Leo hasn't joined: start refused, error goes only to the host.
            host.send_json({"action": "start"})
            assert "Leo" in recv_until(host, lambda m: m["type"] == "error")["message"]

            with client.websocket_connect(f"/ws/{gid}?seat={seat_token(game, 'p3')}") as leo:
                recv_until(leo, is_state)
                host.send_json({"action": "start"})
                for ws in (maya, leo, host, board):
                    st = recv_until(ws, lambda m: is_state(m) and m["state"]["phase"] != "LOBBY")
                    assert st["state"]["phase"] == "WAITING_FOR_ROLL"
                greeting = recv_until(board, lambda m: m["type"] == "mirror" and "Grandma" in m["text"])
                assert "welcome" in greeting["text"].lower()


def test_kid_disconnect_and_rejoin(client, game):
    gid, tok = game["game_id"], seat_token(game, "p2")
    with client.websocket_connect(f"/ws/{gid}?role=board") as board:
        recv_until(board, is_state)
        with client.websocket_connect(f"/ws/{gid}?seat={tok}"):
            assert connected(recv_until(board, is_state), "p2")
        assert not connected(recv_until(board, is_state), "p2")
        with client.websocket_connect(f"/ws/{gid}?seat={tok}") as again:
            assert connected(recv_until(again, is_state), "p2")


def test_second_device_takes_over_seat(client, game):
    gid, tok = game["game_id"], seat_token(game, "p2")
    with client.websocket_connect(f"/ws/{gid}?seat={tok}") as phone:
        recv_until(phone, is_state)
        with client.websocket_connect(f"/ws/{gid}?seat={tok}") as tablet:
            err = recv_until(phone, lambda m: m["type"] == "error")
            assert "taken over" in err["message"]
            with pytest.raises(WebSocketDisconnect) as exc:
                phone.receive_json()
            assert exc.value.code == TAKEN_OVER
            st = recv_until(tablet, is_state)   # new device gets state even with no change
            assert connected(st, "p2")
            # Old socket closing must not mark the seat disconnected.
            tablet.send_json({"action": "start"})
            assert recv_until(tablet, lambda m: m["type"] == "error")
            assert connected(game_state(client, gid), "p2")


def game_state(client, gid):
    with client.websocket_connect(f"/ws/{gid}?role=board") as b:
        return recv_until(b, is_state)


def test_kid_cannot_act_as_someone_else_or_start(client, game):
    gid = game["game_id"]
    with client.websocket_connect(f"/ws/{gid}?seat={seat_token(game, 'p2')}") as maya:
        recv_until(maya, is_state)
        maya.send_json({"player": "p1", "action": "roll"})
        assert "only act as p2" in recv_until(maya, lambda m: m["type"] == "error")["message"]
        maya.send_json({"player": "p2", "action": "start", "force": True})
        assert "host" in recv_until(maya, lambda m: m["type"] == "error")["message"]
        maya.send_json({"source": "vision", "type": "dice", "value": 7})
        assert "dev panel" in recv_until(maya, lambda m: m["type"] == "error")["message"]
        maya.send_text("not json")
        assert "JSON" in recv_until(maya, lambda m: m["type"] == "error")["message"]


def test_dev_panel_reaches_engine_via_adapter(client, game):
    gid = game["game_id"]
    with client.websocket_connect(f"/ws/{gid}?role=board") as board:
        recv_until(board, is_state)
        board.send_json({"source": "vision", "type": "dice", "value": 13})
        assert "2-12" in recv_until(board, lambda m: m["type"] == "error")["message"]
        # Valid read is routed to the engine, which rejects it for now (milestone 3).
        board.send_json({"source": "vision", "type": "dice", "value": 7})
        assert "dice" in recv_until(board, lambda m: m["type"] == "error")["message"]
        board.send_json({"source": "voice", "text": "hmm"})
        assert "yes or no" in recv_until(board, lambda m: m["type"] == "error")["message"]


def test_lan_base(client):
    base = client.get("/api/lan").json()["base"]
    assert base.startswith("http://") and "localhost" not in base
