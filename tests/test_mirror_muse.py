"""Muse Mirror: Spark lines with template fallback; phone 'say'; scene narration from a frame."""
import httpx
from fastapi.testclient import TestClient

from server.adapters.mirror import MuseMirror, TemplateMirror
from server.engine import new_game
from server.main import create_app
from tests.test_store import PLAYERS, recv_until, state_where


class FakeSpark:
    def __init__(self, reply="Grandma twirls into the Royal Ballroom.", status=200):
        self.reply, self.status, self.requests = reply, status, []

    def transport(self):
        def handler(req: httpx.Request) -> httpx.Response:
            self.requests.append(req)
            if self.status != 200:
                return httpx.Response(self.status, json={"error": "nope"})
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": self.reply}}]})
        return httpx.MockTransport(handler)


def started_state():
    s = new_game("g", PLAYERS)
    for p in s.players:
        p.connected = True
    return s


def test_spark_line_replaces_template_and_keeps_minimal_reasoning():
    fake = FakeSpark()
    m = MuseMirror(api_key="k", transport=fake.transport())
    import asyncio
    line = asyncio.run(m.line({"type": "rolled", "player": "p1", "value": 6, "dice": None}, started_state()))
    assert line == "Grandma twirls into the Royal Ballroom."
    body = fake.requests[0].read().decode()
    assert "\"reasoning_effort\":\"minimal\"" in body and "Grandma" in body and "Draft line" in body


def test_prompts_stay_templated():
    fake = FakeSpark()
    m = MuseMirror(api_key="k", transport=fake.transport())
    import asyncio
    s = started_state()
    ev = {"type": "offer", "player": "p1", "square": 9, "price": 120}
    assert asyncio.run(m.line(ev, s)) == asyncio.run(TemplateMirror().line(ev, s))
    assert not fake.requests


def test_falls_back_to_template_on_failure():
    fake = FakeSpark(status=500)
    m = MuseMirror(api_key="k", transport=fake.transport())
    import asyncio
    s = started_state()
    ev = {"type": "rolled", "player": "p1", "value": 6, "dice": None}
    assert asyncio.run(m.line(ev, s)) == asyncio.run(TemplateMirror().line(ev, s))
    assert m.failures == 1


def test_phone_say_is_spoken_at_the_table_and_scene_is_broadcast():
    fake = FakeSpark(reply="Two dice rest on the table beside Grandma's teacup.")
    with TestClient(create_app(sim=True, move_delay=0,
                               mirror_factory=lambda: MuseMirror(api_key="k", transport=fake.transport()))) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        token = game["join_links"]["p2"].rsplit("/", 1)[1]
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board, \
                c.websocket_connect(f"/ws/{game['game_id']}?seat={token}") as maya:
            recv_until(maya, lambda m: m["type"] == "state")
            maya.send_json({"action": "say", "text": "  don't you dare buy   Mirror Hall, Grandma  "})
            m = recv_until(board, lambda m: m["type"] == "mirror" and m.get("kind") == "say")
            assert m["text"] == "Maya says: don't you dare buy Mirror Hall, Grandma" and m["from"] == "p2"
            maya.send_json({"action": "say", "text": "   "})
            assert "say something" in recv_until(maya, lambda m: m["type"] == "error")["message"]
            # a roll with a frame: the Mirror describes the scene to everyone
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            r = c.post("/vision/events", json={"type": "dice_rolled", "values": [1, 2], "total": 3, "frame_b64": "/9j/AAAA"})
            assert r.status_code == 200
            m = recv_until(board, lambda m: m["type"] == "mirror" and m.get("kind") == "scene")
            assert "teacup" in m["text"]


def test_offer_says_price_balance_and_asks_yes_or_no():
    import asyncio
    s = started_state()
    p = s.players[0]
    line = asyncio.run(TemplateMirror().line({"type": "offer", "player": p.id, "square": 9, "price": 120}, s))
    assert line.startswith(f"{p.name}, you have the option to purchase ")
    assert "for 120 gold" in line and f"Your balance is {p.balance} gold." in line
    assert line.endswith("Would you like to purchase this property? Yes or no?")
