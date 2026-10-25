"""The remote step service: relative forward moves on track 1 with a key."""
import asyncio

import httpx

from server.adapters.bridge import StepBeltBridge


class FakeStep:
    def __init__(self, key="k"):
        self.key, self.calls, self.down = key, [], False

    def transport(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if self.down:
                raise httpx.ConnectError("tunnel down")
            self.calls.append((req.method, req.url.path, dict(req.url.params)))
            if req.url.params.get("key") != self.key:
                return httpx.Response(401, json={"detail": "missing or wrong key"})
            return httpx.Response(200, json={"ok": True, "squares": int(req.url.params["squares"])})
        return httpx.MockTransport(handler)


def test_sends_forward_distance_with_key_and_wraps_past_go():
    f = FakeStep()
    b = StepBeltBridge("https://belt.example/", "k", transport=f.transport())
    assert asyncio.run(b.move(1, 0, 7)) == {"type": "move_done", "lane": 1, "square": 7}
    assert f.calls[-1] == ("POST", "/step", {"squares": "7", "key": "k"})
    assert asyncio.run(b.move(1, 38, 2))["square"] == 2
    assert f.calls[-1][2]["squares"] == "4"                       # 38 -> 2 is 4 squares forward
    assert asyncio.run(b.move(1, 5, 5))["type"] == "move_done" and len(f.calls) == 2   # no call for 0 squares


def test_other_lanes_simulated_and_errors_reported():
    f = FakeStep()
    b = StepBeltBridge("https://belt.example", "wrong", lanes=1, transport=f.transport())
    assert asyncio.run(b.move(2, 0, 3))["type"] == "move_done" and not f.calls
    r = asyncio.run(b.move(1, 0, 3))
    assert r["type"] == "move_failed" and "401" in r["reason"] and "wrong key" in r["reason"]
    f.down = True
    assert "unreachable" in asyncio.run(b.move(1, 0, 3))["reason"]
