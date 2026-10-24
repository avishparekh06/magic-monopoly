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
    """Real implementation: Meta Muse Voice Transcribe."""

    def __init__(self) -> None:
        raise NotImplementedError("Muse voice not built yet; run with --sim")

    async def start(self, emit: Emit) -> None:
        raise NotImplementedError
