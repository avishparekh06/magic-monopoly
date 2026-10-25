"""POST /board/move-many on the bench tool: several belts move at once, each with its own tuned values.

arduino/server.py is loaded fresh with its state and tuning files in a temp dir; a fake Board records what is sent."""
import pytest
from fastapi.testclient import TestClient

from server.hardware import motor_server


class FakeBoard:
    def __init__(self, fail_on_call=None, on_wait=None):
        self.calls, self.waits, self.stopped = [], 0, False
        self.fail_on_call, self.on_wait = fail_on_call, on_wait

    def move(self, axis, steps, period_us=None, wait=True):
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("serial dropped")
        self.calls.append((axis, steps, period_us, wait))

    def wait(self, timeout=120):
        self.waits += 1
        if self.on_wait:
            self.on_wait()

    def stop(self):
        self.stopped = True


@pytest.fixture
def hw(monkeypatch, tmp_path):
    monkeypatch.setenv("DRY_RUN", "1")
    m = motor_server(fresh=True)
    monkeypatch.setattr(m, "STATE_FILE", str(tmp_path / "board_state.json"))
    monkeypatch.setattr(m, "TUNING_FILE", str(tmp_path / "tuning.json"))
    for a in range(m.LANES):
        m.CORNERS[a], m.CORNER_PERIODS[a], m.ZONE_COSTS[a], m.ZONES[a] = {}, {}, {}, None
        m.PERIODS[a] = (3000, 1500)
        m.offsets[a] = 0.0
        m.put_square(a, 0)
    m.STEPS[0] = (100, 150, 250)
    m.STEPS[2] = (170, 120, 220)
    return m


def real(hw, board):
    hw.DRY_RUN = False
    hw.board = board
    return TestClient(hw.app)


def test_two_belts_move_together_with_their_own_values(hw):
    fake = FakeBoard()
    r = real(hw, fake).post("/board/move-many", json={"moves": [{"axis": 0, "squares": 3}, {"axis": 2, "squares": 2}]})
    assert r.status_code == 200
    by_axis = {m["axis"]: m for m in r.json()["moves"]}
    # the first hop leaves GO (a corner): corner-out, then normal squares, each axis with its own numbers
    assert by_axis[0]["motor_steps"] == 250 + 2 * 100 and by_axis[2]["motor_steps"] == 220 + 170
    first = fake.calls[:2]                              # round 1: the corner leg of each belt, started together
    assert [(a, s, p) for a, s, p, _ in first] == [(0, 250, 1500), (2, 220, 1500)]
    assert all(wait is False for *_, wait in fake.calls) and fake.waits == 2          # round 2: axis 0's plain squares
    assert sum(s for a, s, *_ in fake.calls if a == 0) == 450 and sum(s for a, s, *_ in fake.calls if a == 2) == 390
    assert (hw.get_square(0), hw.get_square(2), hw.get_square(1)) == (3, 2, 0)
    assert r.json()["moved"] is True


def test_corner_legs_run_in_rounds(hw):
    fake = FakeBoard()
    r = real(hw, fake).post("/board/move-many", json={"moves": [{"axis": 0, "squares": 11}, {"axis": 2, "squares": 1}]})
    assert r.status_code == 200
    # axis 0 crosses corner 10 so it has several segments; axis 2 has one and sits out the later rounds
    assert sum(1 for c in fake.calls if c[0] == 2) == 1 and sum(1 for c in fake.calls if c[0] == 0) > 1
    assert fake.waits == len(r.json()["moves"][0]["segments"])
    assert hw.get_square(0) == 11


def test_dry_run_skips_the_motors_but_saves_squares(hw):
    r = TestClient(hw.app).post("/board/move-many", json={"moves": [{"axis": 0, "squares": 4}, {"axis": 1, "squares": -1}]})
    assert r.status_code == 200 and r.json()["moved"] is False
    assert (hw.get_square(0), hw.get_square(1)) == (4, 39)


def test_busy_lock_is_409(hw):
    client = real(hw, FakeBoard())
    assert hw.lock.acquire(blocking=False)
    try:
        assert client.post("/board/move-many", json={"moves": [{"axis": 0, "squares": 1}]}).status_code == 409
    finally:
        hw.lock.release()


@pytest.mark.parametrize("body", [
    {}, {"moves": []}, {"moves": "x"},
    {"moves": [{"axis": 0}]}, {"moves": [{"axis": "a", "squares": 1}]},
    {"moves": [{"axis": 5, "squares": 1}]}, {"moves": [{"axis": -1, "squares": 1}]},
    {"moves": [{"axis": 0, "squares": 1}, {"axis": 0, "squares": 2}]},
    {"moves": [{"axis": 0, "squares": 41}]},
])
def test_bad_input_is_422(hw, body):
    assert TestClient(hw.app).post("/board/move-many", json=body).status_code == 422
    assert hw.get_square(0) == 0


def test_stop_part_way_saves_nothing_unfinished(hw):
    fake = FakeBoard(on_wait=lambda: hw.stop_requested.set())
    r = real(hw, fake).post("/board/move-many", json={"moves": [{"axis": 0, "squares": 11}, {"axis": 2, "squares": 1}]})
    assert r.status_code == 503 and "Set position" in r.json()["detail"]
    assert hw.get_square(0) == 0                       # axis 0 had more segments to go
    assert hw.get_square(2) == 1                       # axis 2 finished its only segment


def test_serial_failure_is_503_and_names_the_lost_axes(hw):
    r = real(hw, FakeBoard(fail_on_call=1)).post(
        "/board/move-many", json={"moves": [{"axis": 0, "squares": 2}, {"axis": 2, "squares": 2}]})
    assert r.status_code == 503 and "serial dropped" in r.json()["detail"] and "Set position" in r.json()["detail"]
    assert hw.get_square(0) == 0 and hw.get_square(2) == 0
