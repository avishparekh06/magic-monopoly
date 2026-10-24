"""Adapter selection. `--sim` picks the stubs; otherwise the real implementations."""
from __future__ import annotations

from dataclasses import dataclass

from .bridge import GrblBridge, MoveBridge, StubBridge
from .mirror import Mirror, MuseMirror, TemplateMirror
from .vision import ArucoVision, StubVision, Vision
from .voice import MuseVoice, StubVoice, Voice


@dataclass
class Adapters:
    bridge: MoveBridge
    vision: Vision
    voice: Voice
    mirror: Mirror


def make_adapters(sim: bool, move_delay: float = 1.0) -> Adapters:
    if sim:
        return Adapters(StubBridge(move_delay), StubVision(), StubVoice(), TemplateMirror())
    return Adapters(GrblBridge(), ArucoVision(), MuseVoice(), MuseMirror())
