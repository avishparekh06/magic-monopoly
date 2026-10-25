"""The remote belt gateway: /position tells where the token is; /step moves it forward n squares."""
import asyncio

import httpx

from server.adapters.bridge import StepBeltBridge


class FakeGateway:
    def __init__(self, key="k", square=0):
        self.key, self.square, self.calls, self.down = key, square, [], False
        self.position_ok = True

    def transport(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if self.down:
                raise httpx.ConnectError("tunnel down")
            self.calls.append((req.method, req.url.path, dict(req.url.params)))
            if req.url.params.get("key") != self.key:
                return httpx.Response(401, json={"detail": "missing or wrong key"})
            if req.url.path == "/position":
                if not self.position_ok:
                    return httpx.Response(500, json={"detail": "no bench tool"})
                return httpx.Response(200, json={"square": self.square, "name": "x", "type": "property"})
            if req.url.path == "/step":
                n = int(req.url.params["squares"])
                frm = self.square
                self.square = (self.square + n) % 40
                return httpx.Response(200, json={"ok": True, "square": self.square, "name": "x", "type": "property",
                                                 "from": {"id": frm, "name": "y", "type": "property"},
                                                 "squares": n, "motor_steps": 195 * n, "moved": True,
                                                 "passed_go": frm + n >= 40})
            return httpx.Response(404)
        return httpx.MockTransport(handler)


def steps_sent(g):
    return [int(c[2]["squares"]) for c in g.calls if c[1] == "/step"]


def test_steps_forward_distance_and_wraps():
    g = FakeGateway()
    b = StepBeltBridge("https://belt.example/", "k", transport=g.transport())
    assert asyncio.run(b.move(1, 0, 7)) == {"type": "move_done", "lane": 1, "square": 7}
    assert steps_sent(g) == [7] and g.square == 7
    assert asyncio.run(b.move(1, 38, 2)) == {"type": "move_done", "lane": 1, "square": 2}   # gateway was at 7: 5 back is shorter
    assert steps_sent(g)[-1] == -5 and g.square == 2


def test_goes_the_shorter_way_round_and_ties_go_forward():
    g = FakeGateway(square=30)
    b = StepBeltBridge("https://belt.example", "k", transport=g.transport())
    assert asyncio.run(b.move(1, 30, 10))["square"] == 10      # to jail: 20 squares, a tie, goes forward
    assert steps_sent(g) == [20]
    assert asyncio.run(b.move(1, 10, 7))["square"] == 7        # "go back 3": 3 back, not 37 forward
    assert steps_sent(g)[-1] == -3 and g.square == 7
    assert asyncio.run(b.move(1, 7, 38))["square"] == 38       # 31 forward = 9 back, through GO the other way
    assert steps_sent(g)[-1] == -9


def test_self_corrects_from_the_gateways_real_position():
    g = FakeGateway(square=36)                       # stale bench position, game thinks 0
    b = StepBeltBridge("https://belt.example", "k", transport=g.transport())
    r = asyncio.run(b.move(1, 0, 5))
    assert r["type"] == "move_done" and r["square"] == 5
    assert steps_sent(g) == [9] and g.square == 5    # 36 -> 5 is 9 squares forward


def test_position_unavailable_falls_back_to_engine_distance():
    g = FakeGateway(square=3); g.position_ok = False      # token really is on 3; only /position is broken
    b = StepBeltBridge("https://belt.example", "k", transport=g.transport())
    assert asyncio.run(b.move(1, 3, 9))["type"] == "move_done" and steps_sent(g) == [6]


def test_other_lanes_simulated_and_errors_reported():
    g = FakeGateway()
    b = StepBeltBridge("https://belt.example", "wrong", lanes=1, transport=g.transport())
    assert asyncio.run(b.move(2, 0, 3))["type"] == "move_done" and not g.calls
    r = asyncio.run(b.move(1, 0, 3))
    assert r["type"] == "move_failed" and "401" in r["reason"]
    g.down = True
    assert "unreachable" in asyncio.run(b.move(1, 0, 3))["reason"]


def test_reported_square_mismatch_is_a_failed_move():
    g = FakeGateway()
    orig = g.transport()

    def lying(req):
        resp = orig.handler(req)
        if req.url.path == "/step":
            data = resp.json(); data["square"] = 99
            return httpx.Response(200, json=data)
        return resp
    b = StepBeltBridge("https://belt.example", "k", transport=httpx.MockTransport(lying))
    r = asyncio.run(b.move(1, 0, 4))
    assert r["type"] == "move_failed" and "reports square 99" in r["reason"]
