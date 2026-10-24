"""Persistence: games survive a restart, links keep working, stats come from the log."""
import pytest
from fastapi.testclient import TestClient

from server.main import create_app
from server.store import Store

PLAYERS = [
    {"name": "Grandma", "kind": "in_person"},
    {"name": "Maya", "kind": "remote"},
    {"name": "Leo", "kind": "remote"},
]


def recv_until(ws, pred, limit=40):
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("expected message never arrived")


def state_where(ws, pred):
    return recv_until(ws, lambda m: m["type"] == "state" and pred(m["state"]))["state"]


def play_a_bit(client, game):
    """Start, have Grandma roll a 7 (lands on Spells), draw a collect card. Returns the state."""
    token = game["join_links"]["p2"].rsplit("/", 1)[1]
    token3 = game["join_links"]["p3"].rsplit("/", 1)[1]
    with client.websocket_connect(f"/ws/{game['game_id']}?seat={token}") as p2, \
            client.websocket_connect(f"/ws/{game['game_id']}?seat={token3}") as p3, \
            client.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
        state_where(board, lambda s: all(p["connected"] for p in s["players"]))
        board.send_json({"action": "start"})
        state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
        board.send_json({"source": "vision", "type": "dice", "value": 7})
        state_where(board, lambda s: s["phase"] == "WAITING_FOR_CARD")
        board.send_json({"source": "vision", "type": "card", "id": "spell_08"})   # collect 50
        return state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL" and s["current_player"] == "p2")


def test_game_survives_restart(tmp_path):
    db = tmp_path / "games.db"
    with TestClient(create_app(sim=True, move_delay=0, db_path=db)) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        before = play_a_bit(c, game)
        assert before["players"][0]["balance"] == 1550
        assert before["players"][0]["position"] == 7

    # "restart": a new app on the same file
    with TestClient(create_app(sim=True, move_delay=0, db_path=db)) as c:
        listing = c.get("/api/games").json()
        assert [g["game_id"] for g in listing] == [game["game_id"]]
        assert listing[0]["live"] and listing[0]["turn"] == before["turn"]
        assert listing[0]["host_url"].endswith(game["host_token"])

        # the old links still work and the game continues from where it was
        r = c.get(f"/api/games/{game['game_id']}/links", params={"host": game["host_token"]})
        assert r.json()["join_links"] == game["join_links"]
        token = game["join_links"]["p2"].rsplit("/", 1)[1]
        with c.websocket_connect(f"/ws/{game['game_id']}?seat={token}") as p2:
            s = state_where(p2, lambda s: s["players"][1]["connected"])
            assert s["phase"] == "WAITING_FOR_ROLL" and s["current_player"] == "p2"
            assert s["players"][0]["balance"] == 1550 and s["players"][0]["position"] == 7
            assert s["turn"] == before["turn"]
            p2.send_json({"action": "roll"})
            s = state_where(p2, lambda s: s["current_player"] == "p3" or s["phase"] == "WAITING_FOR_DECISION")
            assert s["players"][1]["position"] > 0


def test_mid_move_is_reissued_on_restart(tmp_path):
    """A crash while a token is moving: on resume the move is retried and completes."""
    db = tmp_path / "games.db"
    with TestClient(create_app(sim=True, move_delay=60, db_path=db)) as c:     # move never finishes
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            recv_until(board, lambda m: m["type"] == "state")
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            board.send_json({"source": "vision", "type": "dice", "value": 4})
            state_where(board, lambda s: s["phase"] == "MOVING")
    with TestClient(create_app(sim=True, move_delay=0, db_path=db)) as c:
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            s = state_where(board, lambda s: s["phase"] != "MOVING")
            assert s["players"][0]["position"] == 4


def test_finished_games_are_not_resumed_but_are_listed(tmp_path):
    db = tmp_path / "games.db"
    store = Store(db)
    store.create_game("g1", PLAYERS, None, "h", {"t2": "p2"}, {"phase": "LOBBY", "turn": 0, "players": []})
    store.append("g1", 1, "system", {"action": "x"}, [{"type": "game_over", "winner": "p1"}],
                 {"phase": "GAME_OVER", "turn": 9, "winner": "p1", "players": []})
    store.close()
    with TestClient(create_app(sim=True, db_path=db)) as c:
        listing = c.get("/api/games").json()
        assert listing[0]["status"] == "over" and not listing[0]["live"]
        assert c.get("/api/games/g1/stats").json()["winner"] == "p1"
        assert c.get("/api/games/nope/stats").status_code == 404


def test_stats_from_log(tmp_path):
    db = tmp_path / "games.db"
    with TestClient(create_app(sim=True, move_delay=0, db_path=db)) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        play_a_bit(c, game)
        st = c.get(f"/api/games/{game['game_id']}/stats").json()
        g = next(p for p in st["players"] if p["id"] == "p1")
        assert g["turns"] == 1 and g["roll_count"] == 1 and g["avg_roll"] == 7 and g["cards_drawn"] == 1
        assert st["turns"] == 2 and st["most_landed_square"] == 7
        hist = c.get(f"/api/games/{game['game_id']}/history").json()
        assert hist[0]["action"] == {"action": "new_game"}
        assert any(ev["type"] == "card_drawn" for row in hist for ev in row["events"])
        # presence changes are not history
        assert all(row["action"].get("action") != "set_connected" for row in hist)


def test_default_memory_db_keeps_nothing():
    with TestClient(create_app(sim=True)) as c:
        c.post("/api/games", json={"players": PLAYERS})
    with TestClient(create_app(sim=True)) as c:
        assert c.get("/api/games").json() == []
