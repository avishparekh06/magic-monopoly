"""Voice: Grandma's spoken yes/no answers.

out: {"type": "decision", "player": "p1", "answer": "yes"}
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod

from .vision import Emit

YES = {"yes", "yeah", "yep", "sure", "ok", "okay", "please", "buy"}
NO = {"no", "nope", "nah", "pass", "skip"}


def parse_answer(text: str) -> str | None:
    words = set(re.findall(r"[a-z]+", text.lower()))
    if words & NO:  # check "no" first: "no, please don't" means no
        return "no"
    if words & YES:
        return "yes"
    return None


class Voice(ABC):
    @abstractmethod
    async def start(self, emit: Emit) -> None:
        """Begin listening; call `emit` with a decision event per answer heard."""


class StubVoice(Voice):
    """Answers come from the dev panel's Yes/No buttons or text box."""

    def __init__(self, player: str = "p1") -> None:
        self.player = player
        self._emit: Emit | None = None

    async def start(self, emit: Emit) -> None:
        self._emit = emit

    async def dev_input(self, msg: dict) -> None:
        """msg: {"source": "voice", "answer": "yes"} or {"source": "voice", "text": "yes please"}."""
        answer = msg.get("answer") or parse_answer(msg.get("text", ""))
        if answer not in ("yes", "no"):
            raise ValueError(f"couldn't hear a yes or no in {msg.get('text')!r}")
        if self._emit is None:
            raise ValueError("voice not started")
        await self._emit({"type": "decision", "player": self.player, "answer": answer})


class MuseVoice(Voice):
    """Meta Muse Voice Transcribe (file endpoint).

    The board page records a short clip from the laptop microphone while the
    engine is waiting for a yes/no, and POSTs it to /voice/clip. We send the
    WAV to Muse, parse the transcript into yes/no, and emit a decision.

    Docs: https://dev.meta.ai/docs/speech-to-text
      POST https://api.meta.ai/v1/asr/transcribe   (Authorization: Bearer <key>)
      multipart: request=<json settings>, audio=<wav: mono, 16-bit PCM, 16 or 24 kHz>
      JSON reply: {"sessionId", "transcript", "audioDurationMs", "turns": [...]}
    """

    MODEL = "muse-voice-transcribe-1.0"
    BASE = "https://api.meta.ai/v1"
    KEYWORDS = ["yes", "no", "buy", "pass", "okay", "please"]

    def __init__(self, api_key: str | None = None, base_url: str | None = None, transport=None) -> None:
        import os
        self.api_key = api_key or os.environ.get("MUSE_API_KEY") or os.environ.get("MODEL_API_KEY")
        if not self.api_key:
            raise RuntimeError("MuseVoice needs MUSE_API_KEY (or MODEL_API_KEY) in the environment")
        self.base_url = (base_url or os.environ.get("MUSE_BASE_URL") or self.BASE).rstrip("/")
        self._transport = transport          # httpx transport override for tests
        self._emit: Emit | None = None
        self.last: dict = {}                 # last transcript + answer, for the board page

    async def start(self, emit: Emit) -> None:
        self._emit = emit

    async def transcribe(self, wav: bytes, session_id: str | None = None) -> str:
        """WAV bytes -> transcript text. Raises on HTTP errors."""
        import json
        import httpx
        settings = {"mode": "PUSH_TO_TALK", "model": self.MODEL, "audioEncoding": "WAV",
                    "keywords": self.KEYWORDS}
        files = {"request": (None, json.dumps(settings), "application/json"),
                 "audio": ("clip.wav", wav, "audio/wav")}
        params = {"sessionId": session_id} if session_id else None
        async with httpx.AsyncClient(timeout=20, transport=self._transport) as c:
            r = await c.post(f"{self.base_url}/asr/transcribe", params=params, files=files,
                             headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        text = data.get("transcript") or " ".join(t.get("transcript", "") for t in data.get("turns", []))
        return (text or "").strip()

    async def hear(self, wav: bytes, player: str) -> dict:
        """Transcribe a clip and, if it holds a yes/no, emit that decision for `player`."""
        text = await self.transcribe(wav)
        answer = parse_answer(text)
        self.last = {"transcript": text, "answer": answer, "player": player}
        if answer and self._emit:
            await self._emit({"type": "decision", "player": player, "answer": answer})
        return self.last
