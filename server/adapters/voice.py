"""Voice: Grandma's spoken yes/no answers.

out: {"type": "decision", "player": "p1", "answer": "yes"}
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod

from .vision import Emit

YES = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "buy", "purchase", "absolutely", "definitely"}
NO = {"no", "nope", "nah", "pass", "skip"}
NEGATIONS = {"don", "dont", "not", "never"}   # "don't buy it" is a no even though it says "buy"


def parse_answer(text: str) -> str | None:
    """A clear yes or no, or None. Any negation means no; otherwise the first yes/no word decides
    ("yes, no problem" is a yes). Not "please": "no please" and "please do" both contain it."""
    words = re.findall(r"[a-z]+", text.lower())
    if NEGATIONS & set(words):
        return "no"
    return next(("yes" if w in YES else "no" for w in words if w in YES or w in NO), None)


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
        await self._emit({"type": "decision", "answer": answer})      # whoever is deciding


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
    CLASSIFY_MODEL = "muse-spark-1.3"
    BASE = "https://api.meta.ai/v1"
    KEYWORDS = ["yes", "no", "buy", "purchase", "pass", "okay"]

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

    async def classify(self, text: str, question: str = "") -> str | None:
        """Muse Spark reads an answer the word list could not ("I'll take it", "maybe later").
        "yes" / "no", or None when Spark is unsure or the call fails: the Mirror then asks again."""
        import httpx
        system = ("A player in a board game was asked a yes-or-no question and answered aloud. "
                  "Reply with exactly one word: yes, no, or unclear. Say unclear unless the answer clearly means yes or no.")
        body = {"model": self.CLASSIFY_MODEL, "max_tokens": 800, "temperature": 0, "reasoning_effort": "minimal",
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": f"Question: {question or 'Buy this place?'}\nAnswer: {text}"}]}
        try:
            async with httpx.AsyncClient(timeout=4, transport=self._transport) as c:
                r = await c.post(f"{self.base_url}/chat/completions", json=body,
                                 headers={"Authorization": f"Bearer {self.api_key}"})
            r.raise_for_status()
            reply = (r.json()["choices"][0]["message"].get("content") or "").strip().lower()
        except Exception:
            return None
        word = re.findall(r"[a-z]+", reply)[:1]
        return word[0] if word and word[0] in ("yes", "no") else None

    async def hear(self, wav: bytes, player: str, question: str = "") -> dict:
        """Transcribe a clip and, if it holds a yes/no, emit that decision for `player`.
        Clear answers are decided by the word list; anything else is read by Spark."""
        text = await self.transcribe(wav)
        answer = parse_answer(text)
        if answer is None and text:
            answer = await self.classify(text, question)
        self.last = {"transcript": text, "answer": answer, "player": player}
        if answer and self._emit:
            await self._emit({"type": "decision", "player": player, "answer": answer})
        return self.last
