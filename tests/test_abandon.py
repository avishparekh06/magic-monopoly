"""Abandoned games stay in history but are not resumed and their links stop working."""
from fastapi.testclient import TestClient

from server.main import create_app
from tests.test_store import PLAYERS


def test_abandon_removes_room_and_is_not_resumed(tmp_path):
    db = tmp_path / "g.db"
    with TestClient(create_app(sim=True, move_delay=0, db_path=db)) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        other = c.post("/api/games", json={"players": PLAYERS}).json()
        assert c.post(f"/api/games/{game['game_id']}/abandon", params={"host": "nope"}).status_code == 403
        r = c.post(f"/api/games/{game['game_id']}/abandon", params={"host": game["host_token"]})
        assert r.json()["status"] == "abandoned"
        assert c.get(f"/api/games/{game['game_id']}/links", params={"host": game["host_token"]}).status_code == 404
        listing = {g["game_id"]: g for g in c.get("/api/games").json()}
        assert listing[game["game_id"]]["status"] == "abandoned" and not listing[game["game_id"]]["live"]
        assert listing[other["game_id"]]["live"]
        assert c.get(f"/api/games/{game['game_id']}/stats").status_code == 200    # history kept
    with TestClient(create_app(sim=True, move_delay=0, db_path=db)) as c:
        listing = {g["game_id"]: g for g in c.get("/api/games").json()}
        assert not listing[game["game_id"]]["live"] and listing[other["game_id"]]["live"]
        assert c.post(f"/api/games/{game['game_id']}/abandon", params={"host": game["host_token"]}).status_code == 200  # idempotent
