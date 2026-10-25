"""Demo mode: jog the belt by hand, then resume a saved game by driving the token to its square."""
import time

import pytest
from fastapi.testclient import TestClient

from server.hardware import motor_server
from server.main import create_app
from tests.test_ws import PLAYERS


@pytest.fixture
def hw(monkeypatch, tmp_path):
    monkeypatch.setenv("DRY_RUN", "1")
    module = motor_server(fresh=True)
    monkeypatch.setattr(module, "STATE_FILE", str(tmp_path / "board_state.json"))
    module.current_square = 0
    module.offsets.update({axis: 0.0 for axis in module.offsets})
    return module


def app_client(**kw):
    return TestClient(create_app(sim=True, move_delay=0, bridge="arduino", **kw))


def test_jog_routes_exist_only_in_dev(hw):
    with app_client() as c:
        assert c.post("/jog/stop").status_code == 404
        assert c.get("/api/config").json()["jog"] is False
    with app_client(dev=True) as c:
        assert c.get("/api/config").json()["jog"] is True
        assert c.post("/jog/stop").status_code == 200


def test_start_holds_the_motor_until_stop(hw):
    with app_client(dev=True) as c:
        assert c.post("/jog/start", params={"direction": "back"}).json()["running"] is True
        assert c.post("/jog/start").status_code == 409          # already running
        assert c.post("/jog/nudge", params={"steps": 50}).status_code == 409
        assert c.post("/board/goto", params={"square": 3, "direction": "forward"}).status_code == 409
        assert c.post("/jog/stop").json() == {"running": False}
        assert c.post("/jog/nudge", params={"steps": 50}).json()["steps"] == 50
        assert c.post("/jog/nudge", params={"steps": -50}).status_code == 200
        assert c.post("/jog/nudge", params={"steps": 99999}).status_code == 422
        assert c.post("/jog/start", params={"direction": "sideways"}).status_code == 422


def test_this_is_go_resets_the_tracked_square(hw):
    hw.put_square(hw.MOTOR_AXIS, 7)
    with app_client(dev=True) as c:
        assert c.post("/jog/home").json()["square"] == 0
        assert c.get("/board").json()["square"] == 0


def test_demo_resume_needs_dev_and_a_known_game(hw):
    with app_client() as c:
        assert c.post("/api/games/nope/demo-resume").status_code == 404
    with app_client(dev=True) as c:
        assert c.post("/api/games/nope/demo-resume").status_code == 404


def test_demo_resume_drives_the_token_from_go_to_its_saved_square(hw):
    with app_client(dev=True) as c:
        gid = c.post("/api/games", json={"players": PLAYERS}).json()["game_id"]
        room = c.app.state.rooms[gid]
        room.state.players[0].position = 6
        hw.put_square(hw.MOTOR_AXIS, 12)                        # belt thinks it is somewhere else
        assert c.post(f"/api/games/{gid}/demo-resume").json()["board_url"] == f"/board/{gid}"
        for _ in range(50):
            if hw.get_square(hw.MOTOR_AXIS) == 6:
                break
            time.sleep(0.1)
        assert hw.get_square(hw.MOTOR_AXIS) == 6
