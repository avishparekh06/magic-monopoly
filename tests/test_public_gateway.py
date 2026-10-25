"""The public gateway: one key-protected step action, now forward or back, up to half the board."""
import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient

PATH = Path(__file__).resolve().parent.parent / "arduino" / "public_gateway.py"


def load(monkeypatch):
    monkeypatch.setenv("PUBLIC_KEY", "k")
    spec = importlib.util.spec_from_file_location("public_gateway_under_test", PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    sent = []

    async def ask_bench(method, path, **params):
        sent.append((method, path, params))
        return {"from": 5, "to": {"square": 2}, "squares": params.get("squares"),
                "motor_steps": 0, "moved": True, "passed_go": False}
    mod.ask_bench = ask_bench
    return mod, sent


def test_backward_and_half_board_steps_are_allowed(monkeypatch):
    mod, sent = load(monkeypatch)
    c = TestClient(mod.app)
    assert c.post("/step", params={"squares": -3, "key": "k"}).status_code == 200
    assert c.post("/step", params={"squares": 20, "key": "k"}).status_code == 200
    assert [s[2]["squares"] for s in sent] == [-3, 20]


def test_zero_and_more_than_half_the_board_are_refused(monkeypatch):
    mod, sent = load(monkeypatch)
    c = TestClient(mod.app)
    for n in (0, 21, -21):
        assert c.post("/step", params={"squares": n, "key": "k"}).status_code == 422
    assert not sent


def test_the_key_is_still_required(monkeypatch):
    mod, sent = load(monkeypatch)
    assert TestClient(mod.app).post("/step", params={"squares": -3}).status_code == 401
