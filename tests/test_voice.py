"""Muse voice: WAV clip -> transcript -> yes/no decision, with the Muse HTTP call mocked."""
import asyncio
import io
import struct
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from server.adapters.voice import MuseVoice, parse_answer
from server.main import create_app
from tests.test_store import PLAYERS, recv_until, state_where


def wav_bytes(seconds=0.5, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))
    return buf.getvalue()


class FakeMuse:
    """Records the request Muse would receive and answers with a canned transcript."""

    def __init__(self, transcript):
        self.transcript, self.requests = transcript, []

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json={"sessionId": "s1", "transcript": self.transcript,
                                             "audioDurationMs": 500, "turns": []})
        return httpx.MockTransport(handler)


def test_transcribe_sends_documented_multipart():
    fake = FakeMuse("yes please")
    v = MuseVoice(api_key="k", transport=fake.transport())
    assert asyncio.run(v.transcribe(wav_bytes())) == "yes please"
    req = fake.requests[0]
    assert req.url.path == "/v1/asr/transcribe" and req.headers["authorization"] == "Bearer k"
    body = req.read()
    assert b'name="request"' in body and b'"model": "muse-voice-transcribe-1.0"' in body
    assert b'name="audio"' in body and b"RIFF" in body


def test_parse_answer():
    assert parse_answer("Yes please!") == "yes"
    assert parse_answer("no thank you") == "no"
    assert parse_answer("hmm let me think") is None
    assert parse_answer("no, don't buy it") == "no"


def test_clip_endpoint_decides_for_the_player_on_offer():
    fake = FakeMuse("yes, buy it")
    with TestClient(create_app(sim=True, move_delay=0, seed=1,
                               voice_factory=lambda: MuseVoice(api_key="k", transport=fake.transport()))) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        # nobody deciding yet
        assert c.post("/voice/clip", content=wav_bytes()).json()["ok"] is False
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            recv_until(board, lambda m: m["type"] == "state")
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            board.send_json({"source": "vision", "type": "dice", "value": 3})       # Gingerbread House, 60 gold
            s = state_where(board, lambda s: s["phase"] == "WAITING_FOR_DECISION")
            assert s["pending"]["player"] == "p1"
            r = c.post("/voice/clip", content=wav_bytes()).json()
            assert r["ok"] and r["transcript"] == "yes, buy it" and r["answer"] == "yes"
            s = state_where(board, lambda s: s["phase"] in ("WAITING_FOR_PAYMENT", "WAITING_FOR_ROLL"))
            assert s["pending"] is None or s["pending"]["reason"] == "buy"


def test_unclear_clip_changes_nothing():
    fake = FakeMuse("what did you say")
    with TestClient(create_app(sim=True, move_delay=0, seed=1,
                               voice_factory=lambda: MuseVoice(api_key="k", transport=fake.transport()))) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            recv_until(board, lambda m: m["type"] == "state")
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            board.send_json({"source": "vision", "type": "dice", "value": 3})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_DECISION")
            r = c.post("/voice/clip", content=wav_bytes()).json()
            assert r["ok"] and r["answer"] is None
            assert c.get(f"/api/games/{game['game_id']}/stats").json()["phase"] == "WAITING_FOR_DECISION"
            assert c.post("/voice/clip", content=b"tiny").status_code == 422
            assert c.post("/voice/clip", content=wav_bytes(), headers={"x-forwarded-for": "1.2.3.4"}).status_code == 403


def test_stub_voice_server_refuses_clips():
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            recv_until(board, lambda m: m["type"] == "state")
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            board.send_json({"source": "vision", "type": "dice", "value": 3})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_DECISION")
            assert c.post("/voice/clip", content=wav_bytes()).status_code == 503


def test_muse_voice_needs_a_key(monkeypatch):
    monkeypatch.delenv("MUSE_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        MuseVoice()


def test_dev_panel_answers_work_with_muse_voice():
    """Yes/No buttons and typed text still decide when the real voice adapter is in use."""
    fake = FakeMuse("unused")
    with TestClient(create_app(sim=True, move_delay=0, seed=1,
                               voice_factory=lambda: MuseVoice(api_key="k", transport=fake.transport()))) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            recv_until(board, lambda m: m["type"] == "state")
            board.send_json({"action": "start", "force": True})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            board.send_json({"source": "vision", "type": "dice", "value": 3})
            state_where(board, lambda s: s["phase"] == "WAITING_FOR_DECISION")
            board.send_json({"source": "voice", "text": "no thanks"})
            s = state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            assert s["current_player"] == "p2" and s["players"][0]["properties"] == []


def test_parse_answer_first_word_and_negation():
    assert parse_answer("yes, no problem") == "yes"
    assert parse_answer("I don't want to buy it") == "no"
    assert parse_answer("not now") == "no"
    assert parse_answer("please") is None
    assert parse_answer("I'll take it") is None          # left to Spark


class FakeMuseWithSpark(FakeMuse):
    """Transcribe answers with `transcript`; chat completions (Spark) answers with `verdict`."""

    def __init__(self, transcript, verdict):
        super().__init__(transcript)
        self.verdict = verdict

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.url.path.endswith("/chat/completions"):
                return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                              "message": {"content": self.verdict}}]})
            return httpx.Response(200, json={"sessionId": "s1", "transcript": self.transcript,
                                             "audioDurationMs": 500, "turns": []})
        return httpx.MockTransport(handler)


def offered(c, board):
    recv_until(board, lambda m: m["type"] == "state")
    board.send_json({"action": "start", "force": True})
    state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
    board.send_json({"source": "vision", "type": "dice", "value": 3})       # Gingerbread House, 60 gold
    return state_where(board, lambda s: s["phase"] == "WAITING_FOR_DECISION")


def test_spark_reads_an_answer_the_word_list_cannot():
    fake = FakeMuseWithSpark("I'll take it", "Yes")
    with TestClient(create_app(sim=True, move_delay=0, seed=1,
                               voice_factory=lambda: MuseVoice(api_key="k", transport=fake.transport()))) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            offered(c, board)
            r = c.post("/voice/clip", content=wav_bytes()).json()
            assert r["answer"] == "yes"
            spark = [q for q in fake.requests if q.url.path.endswith("/chat/completions")]
            assert len(spark) == 1 and b"Gingerbread House" in spark[0].read()
            s = state_where(board, lambda s: s["phase"] == "WAITING_FOR_ROLL")
            assert s["players"][0]["properties"] == [3]      # Gingerbread House is square 3


def test_clear_answer_skips_spark():
    fake = FakeMuseWithSpark("no thanks", "yes")
    with TestClient(create_app(sim=True, move_delay=0, seed=1,
                               voice_factory=lambda: MuseVoice(api_key="k", transport=fake.transport()))) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            offered(c, board)
            assert c.post("/voice/clip", content=wav_bytes()).json()["answer"] == "no"
            assert not [q for q in fake.requests if q.url.path.endswith("/chat/completions")]


def test_unclear_answer_is_asked_again_aloud():
    fake = FakeMuseWithSpark("hmm let me think", "unclear")
    with TestClient(create_app(sim=True, move_delay=0, seed=1,
                               voice_factory=lambda: MuseVoice(api_key="k", transport=fake.transport()))) as c:
        game = c.post("/api/games", json={"players": PLAYERS}).json()
        with c.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
            offered(c, board)
            assert c.post("/voice/clip", content=wav_bytes()).json()["answer"] is None
            m = recv_until(board, lambda m: m["type"] == "mirror" and "didn't catch" in m.get("text", ""))
            assert "Gingerbread House" in m["text"] and m["text"].endswith("Yes or no?")
            assert c.get(f"/api/games/{game['game_id']}/stats").json()["phase"] == "WAITING_FOR_DECISION"
